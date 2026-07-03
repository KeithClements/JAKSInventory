"""Settings screen for JAKS Inventory app — tabbed layout."""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, QThread, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QCheckBox,
    QComboBox,
    QMessageBox,
    QTextEdit,
    QTabWidget,
    QSpinBox,
    QDoubleSpinBox,
    QFileDialog,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QApplication,
    QInputDialog,
    QDialog,
)

from db import get_setting, set_setting, get_all_settings
from jaks_inventory.utils.email_worker import EmailWorker

# Import QBO client for connection test
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
try:
    from qbo.client import QBOClient
    QBO_AVAILABLE = True
except ImportError:
    QBO_AVAILABLE = False

log = logging.getLogger(__name__)


class SettingsScreen(QWidget):
    """Settings screen with tabbed layout."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._load_settings()

    def _build_ui(self) -> None:
        layout = QVBoxLayout()

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_company_tab(), "Company")
        self._tabs.addTab(self._build_invoice_tab(), "Invoice")
        self._tabs.addTab(self._build_inventory_tab(), "Inventory")
        self._tabs.addTab(self._build_po_tab(), "📋 Purchase Orders")
        self._tabs.addTab(self._build_email_tab(), "📧 Email / SMTP")
        self._tabs.addTab(self._build_qbo_tab(), "QBO")
        self._tabs.addTab(self._build_system_tab(), "System")
        self._tabs.addTab(self._build_appearance_tab(), "🎨 Appearance")
        self._tabs.addTab(self._build_scraper_tab(), "Scraper")

        layout.addWidget(self._tabs)
        self.setLayout(layout)

    # ------------------------------------------------------------------
    # Company Tab
    # ------------------------------------------------------------------
    def _build_company_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout()
        form = QFormLayout()

        self._company_name = QLineEdit()
        self._company_name.setPlaceholderText("e.g. JAK'S Diesel")
        form.addRow("Company Name:", self._company_name)

        self._company_address = QLineEdit()
        self._company_address.setPlaceholderText("Street address")
        form.addRow("Address:", self._company_address)

        city_state_row = QHBoxLayout()
        self._company_city = QLineEdit()
        self._company_city.setPlaceholderText("City")
        self._company_state = QLineEdit()
        self._company_state.setPlaceholderText("ST")
        self._company_state.setMaximumWidth(60)
        self._company_zip = QLineEdit()
        self._company_zip.setPlaceholderText("ZIP")
        self._company_zip.setMaximumWidth(80)
        city_state_row.addWidget(self._company_city, 3)
        city_state_row.addWidget(self._company_state, 1)
        city_state_row.addWidget(self._company_zip, 1)
        form.addRow("City / State / ZIP:", city_state_row)

        self._company_phone = QLineEdit()
        self._company_phone.setPlaceholderText("(702) 555-0100")
        form.addRow("Phone:", self._company_phone)

        self._company_email = QLineEdit()
        self._company_email.setPlaceholderText("parts@example.com")
        form.addRow("Email:", self._company_email)

        self._company_website = QLineEdit()
        self._company_website.setPlaceholderText("www.example.com")
        form.addRow("Website:", self._company_website)

        # Logo
        logo_row = QHBoxLayout()
        self._logo_path = QLineEdit()
        self._logo_path.setPlaceholderText("assets/logo.png")
        self._logo_path.setReadOnly(True)
        logo_browse = QPushButton("Browse...")
        logo_browse.clicked.connect(self._browse_logo)
        logo_row.addWidget(self._logo_path, 1)
        logo_row.addWidget(logo_browse)
        form.addRow("Logo:", logo_row)

        self._logo_preview = QLabel()
        self._logo_preview.setFixedSize(120, 80)
        self._logo_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._logo_preview.setStyleSheet("border: 1px solid #555; background: #f5f5f5;")
        form.addRow("", self._logo_preview)

        info = QLabel("These fields appear on PDF invoices, statements, and reports.")
        info.setStyleSheet("color: gray; font-style: italic;")
        form.addRow("", info)

        layout.addLayout(form)
        layout.addStretch()

        save_btn = QPushButton("Save Company Info")
        save_btn.setMaximumWidth(200)
        save_btn.clicked.connect(self._save_company)
        layout.addWidget(save_btn, alignment=Qt.AlignmentFlag.AlignRight)

        w.setLayout(layout)
        return w

    # ------------------------------------------------------------------
    # Invoice Tab
    # ------------------------------------------------------------------
    def _build_invoice_tab(self) -> QWidget:
        # Outer widget returned to the tab — contains only the scroll area
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer_layout.addWidget(scroll)

        # Inner widget holds all the content
        w = QWidget()
        layout = QVBoxLayout()
        form = QFormLayout()

        self._tax_rate = QDoubleSpinBox()
        self._tax_rate.setRange(0, 20)
        self._tax_rate.setDecimals(3)
        self._tax_rate.setSuffix(" %")
        form.addRow("Default Tax Rate:", self._tax_rate)

        self._payment_terms = QComboBox()
        self._payment_terms.addItems(["COD", "Due on Receipt", "Net 15", "Net 30", "Net 45", "Net 60"])
        form.addRow("Payment Terms:", self._payment_terms)

        self._default_customer_terms = QComboBox()
        self._default_customer_terms.addItems(["COD", "Due on Receipt", "Net 15", "Net 30", "Net 45", "Net 60"])
        form.addRow("Default Customer Terms:", self._default_customer_terms)

        self._invoice_prefix = QLineEdit()
        self._invoice_prefix.setPlaceholderText("INV-")
        self._invoice_prefix.setMaximumWidth(120)
        form.addRow("Invoice Prefix:", self._invoice_prefix)

        self._invoice_notes = QTextEdit()
        self._invoice_notes.setPlaceholderText("Default notes shown on invoices...")
        self._invoice_notes.setMaximumHeight(60)
        form.addRow("Default Notes:", self._invoice_notes)

        self._show_core_charges = QCheckBox("Display core charge line on PDF invoices")
        form.addRow("Core Charges:", self._show_core_charges)

        layout.addLayout(form)

        # ── Sales Tax ──
        tax_group = QGroupBox("Sales Tax")
        tax_gform = QFormLayout()

        self._tax_home_state = QLineEdit()
        self._tax_home_state.setPlaceholderText("e.g. NV")
        self._tax_home_state.setMaximumWidth(60)
        self._tax_home_state.setToolTip(
            "Two-letter state code. Tax is applied to invoices shipped within this state."
        )
        tax_gform.addRow("Home State (in-state tax):", self._tax_home_state)

        self._tax_ship_from_zip = QLineEdit()
        self._tax_ship_from_zip.setPlaceholderText("e.g. 89002")
        self._tax_ship_from_zip.setMaximumWidth(80)
        self._tax_ship_from_zip.setToolTip(
            "Ship-from ZIP used for future tax lookup integrations."
        )
        tax_gform.addRow("Ship-From ZIP:", self._tax_ship_from_zip)

        self._tax_destination_sourced = QCheckBox(
            "Destination-sourced (ship-to state determines tax)"
        )
        self._tax_destination_sourced.setToolTip(
            "When checked, tax is based on where the product ships TO. "
            "When unchecked, tax is based on your home state only."
        )
        tax_gform.addRow("Sourcing Rule:", self._tax_destination_sourced)

        # Tax provider (manual table vs. TaxJar API)
        self._tax_provider = QComboBox()
        self._tax_provider.addItems(["manual", "taxjar"])
        self._tax_provider.setToolTip(
            "manual = use the rate table below.\n"
            "taxjar = call TaxJar API by ship-to ZIP and cache results "
            "in the table for offline fallback."
        )
        tax_gform.addRow("Tax Provider:", self._tax_provider)

        self._taxjar_api_key = QLineEdit()
        self._taxjar_api_key.setPlaceholderText("TaxJar API key (paste from TaxJar dashboard)")
        self._taxjar_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        tax_gform.addRow("TaxJar API Key:", self._taxjar_api_key)

        self._taxjar_sandbox = QCheckBox("Use TaxJar Sandbox (testing only)")
        tax_gform.addRow("", self._taxjar_sandbox)

        taxjar_btn_row = QHBoxLayout()
        self._taxjar_test_btn = QPushButton("🧪 Test TaxJar Connection")
        self._taxjar_test_btn.clicked.connect(self._test_taxjar)
        self._taxjar_status = QLabel("")
        taxjar_btn_row.addWidget(self._taxjar_test_btn)
        taxjar_btn_row.addWidget(self._taxjar_status, 1)
        tax_gform.addRow(taxjar_btn_row)

        # Editable rate table — state + optional ZIP overrides
        rates_label = QLabel(
            "Per-state and per-ZIP tax rates. Leave ZIP blank for the state\n"
            "default. Most-specific ZIP match wins. States not in this table\n"
            "are treated as no nexus (0% tax)."
        )
        rates_label.setStyleSheet("color: #555; font-style: italic;")
        rates_label.setWordWrap(True)
        tax_gform.addRow(rates_label)

        self._tax_table = QTableWidget(0, 5)
        self._tax_table.setHorizontalHeaderLabels(
            ["State", "ZIP (blank = state default)", "Rate %", "QBO Tax Code", "Description"]
        )
        self._tax_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch
        )
        self._tax_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._tax_table.setMinimumHeight(180)
        tax_gform.addRow(self._tax_table)

        rate_btn_row = QHBoxLayout()
        add_rate_btn = QPushButton("➕ Add Rate")
        add_rate_btn.clicked.connect(self._add_tax_rate_row)
        del_rate_btn = QPushButton("🗑 Delete Selected")
        del_rate_btn.clicked.connect(self._delete_tax_rate_row)
        save_rates_btn = QPushButton("💾 Save Tax Rates")
        save_rates_btn.clicked.connect(self._save_tax_rates)
        rate_btn_row.addWidget(add_rate_btn)
        rate_btn_row.addWidget(del_rate_btn)
        rate_btn_row.addWidget(save_rates_btn)
        rate_btn_row.addStretch()
        tax_gform.addRow(rate_btn_row)

        tax_group.setLayout(tax_gform)
        layout.addWidget(tax_group)

        # ── Quote / Sales Order / Invoice Display ──
        display_group = QGroupBox("Quote / SO / Invoice Display")
        display_form = QFormLayout()

        self._quote_show_sku = QCheckBox("Show SKU column")
        self._quote_show_sku.setToolTip("Show or hide the SKU column on quotes, sales orders, and invoices")
        display_form.addRow("SKU:", self._quote_show_sku)

        self._quote_show_warranty = QCheckBox("Show product warranty below description")
        self._quote_show_warranty.setToolTip("Display warranty info in small print below each line item")
        self._quote_show_warranty.setChecked(True)
        display_form.addRow("Warranty:", self._quote_show_warranty)

        self._quote_show_core = QCheckBox("Show Core column")
        self._quote_show_core.setChecked(True)
        display_form.addRow("Core Column:", self._quote_show_core)

        self._quote_show_notes = QCheckBox("Show line notes / ETA")
        self._quote_show_notes.setChecked(True)
        display_form.addRow("Notes:", self._quote_show_notes)

        self._quote_show_freight = QCheckBox("Show per-line freight")
        self._quote_show_freight.setChecked(True)
        display_form.addRow("Freight:", self._quote_show_freight)

        self._quote_show_company_address = QCheckBox("Show our company address in PDF header")
        self._quote_show_company_address.setChecked(True)
        display_form.addRow("Company Address:", self._quote_show_company_address)

        self._quote_show_supplier = QCheckBox("Show supplier under each line description")
        display_form.addRow("Supplier:", self._quote_show_supplier)

        from PySide6.QtWidgets import QSpinBox as _QSpinBox
        self._quote_logo_size_pct = _QSpinBox()
        self._quote_logo_size_pct.setRange(50, 150)
        self._quote_logo_size_pct.setSingleStep(5)
        self._quote_logo_size_pct.setSuffix(" %")
        self._quote_logo_size_pct.setValue(100)
        self._quote_logo_size_pct.setToolTip(
            "Scale the PDF header logo. 100% = 2.6\" wide × 1.1\" tall.")
        display_form.addRow("Logo Size:", self._quote_logo_size_pct)

        self._quote_default_terms = QLineEdit()
        self._quote_default_terms.setPlaceholderText("e.g. Net 30")
        display_form.addRow("Default Terms (new quotes):", self._quote_default_terms)

        self._quote_default_expires_days = _QSpinBox()
        self._quote_default_expires_days.setRange(0, 365)
        self._quote_default_expires_days.setValue(30)
        display_form.addRow("Default Quote Validity (days):", self._quote_default_expires_days)

        display_group.setLayout(display_form)
        layout.addWidget(display_group)

        # ── Shipping & Delivery ──
        shipping_group = QGroupBox("Shipping & Delivery")
        shipping_form = QFormLayout()

        self._business_hq_zip = QLineEdit()
        self._business_hq_zip.setPlaceholderText("e.g. 82001")
        self._business_hq_zip.setMaxLength(10)
        self._business_hq_zip.setToolTip(
            "Your business HQ ZIP code. Used to estimate the fuel/delivery "
            "surcharge tier from the customer's ZIP at quote-send time."
        )
        shipping_form.addRow("Business HQ ZIP:", self._business_hq_zip)

        self._default_ship_method = QComboBox()
        self._default_ship_method.setEditable(True)
        self._default_ship_method.addItems([
            "Will Call",
            "Local Delivery",
            "Ground",
            "UPS Ground",
            "FedEx Ground",
            "Freight",
            "USPS Priority",
            "Overnight",
        ])
        self._default_ship_method.setToolTip(
            "Default shipping method pre-selected on new quotes / invoices. "
            "Pick from the list or type your own."
        )
        shipping_form.addRow("Default Shipping Method:", self._default_ship_method)

        self._default_ship_fee = QDoubleSpinBox()
        self._default_ship_fee.setRange(0, 9999.99)
        self._default_ship_fee.setDecimals(2)
        self._default_ship_fee.setPrefix("$ ")
        self._default_ship_fee.setSingleStep(5.0)
        self._default_ship_fee.setToolTip(
            "Flat shipping fee added to new quotes / invoices when this method is "
            "selected. Set 0 for free / TBD."
        )
        shipping_form.addRow("Default Shipping Fee:", self._default_ship_fee)

        self._default_ship_taxable = QCheckBox("Shipping is taxable")
        self._default_ship_taxable.setToolTip(
            "Include the shipping fee in the taxable subtotal when generating "
            "invoices. Most states tax shipping; verify with your accountant."
        )
        shipping_form.addRow("Tax on Shipping:", self._default_ship_taxable)

        shipping_group.setLayout(shipping_form)
        layout.addWidget(shipping_group)

        layout.addStretch()

        save_btn = QPushButton("Save Invoice Defaults")
        save_btn.setMaximumWidth(200)
        save_btn.clicked.connect(self._save_invoice)
        layout.addWidget(save_btn, alignment=Qt.AlignmentFlag.AlignRight)

        w.setLayout(layout)
        scroll.setWidget(w)
        return outer

    # ------------------------------------------------------------------
    # Inventory Tab
    # ------------------------------------------------------------------
    def _build_inventory_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout()
        form = QFormLayout()

        self._sku_prefix = QLineEdit()
        self._sku_prefix.setPlaceholderText("JAKS-")
        self._sku_prefix.setMaximumWidth(150)
        form.addRow("SKU Prefix:", self._sku_prefix)

        self._low_stock_threshold = QSpinBox()
        self._low_stock_threshold.setRange(0, 9999)
        self._low_stock_threshold.setSuffix(" units")
        form.addRow("Low Stock Threshold:", self._low_stock_threshold)

        self._reorder_formula = QComboBox()
        self._reorder_formula.addItems(["Max - On Hand", "Fixed Qty"])
        form.addRow("Reorder Formula:", self._reorder_formula)

        self._auto_barcode = QCheckBox("Auto-generate barcode on new product")
        form.addRow("Barcodes:", self._auto_barcode)

        self._so_manual_po_mode = QCheckBox(
            "Manual PO mode (always prompt before creating Purchase Orders)"
        )
        self._so_manual_po_mode.setToolTip(
            "Recommended for production: prevents implicit PO creation from guided SO actions."
        )
        form.addRow("Sales Orders:", self._so_manual_po_mode)

        layout.addLayout(form)

        # ── Sales Velocity Settings ──
        velocity_group = QGroupBox("Sales Velocity / Auto Min-Max")
        vel_form = QFormLayout()

        self._velocity_lookback = QSpinBox()
        self._velocity_lookback.setRange(7, 365)
        self._velocity_lookback.setSuffix(" days")
        self._velocity_lookback.setToolTip("How far back to look when calculating sales velocity")
        vel_form.addRow("Lookback Period:", self._velocity_lookback)

        self._safety_stock_mult = QDoubleSpinBox()
        self._safety_stock_mult.setRange(0.5, 5.0)
        self._safety_stock_mult.setDecimals(1)
        self._safety_stock_mult.setSingleStep(0.5)
        self._safety_stock_mult.setToolTip("Multiplier applied to lead-time demand for safety stock")
        vel_form.addRow("Safety Stock Multiplier:", self._safety_stock_mult)

        self._default_lead_time = QSpinBox()
        self._default_lead_time.setRange(1, 180)
        self._default_lead_time.setSuffix(" days")
        self._default_lead_time.setToolTip("Default lead time when vendor has none set")
        vel_form.addRow("Default Lead Time:", self._default_lead_time)

        velocity_group.setLayout(vel_form)
        layout.addWidget(velocity_group)

        # ── QOH Color Thresholds (Quotes) ──
        qoh_group = QGroupBox("QOH Color Thresholds (Quotes)")
        qoh_form = QFormLayout()

        self._qoh_yellow_threshold = QSpinBox()
        self._qoh_yellow_threshold.setRange(0, 9999)
        self._qoh_yellow_threshold.setSuffix(" units")
        self._qoh_yellow_threshold.setToolTip(
            "QOH at or below this → yellow warning.\n"
            "QOH at 0 is always red. Above this → green."
        )
        qoh_form.addRow("Yellow Threshold:", self._qoh_yellow_threshold)

        qoh_group.setLayout(qoh_form)
        layout.addWidget(qoh_group)

        layout.addStretch()

        save_btn = QPushButton("Save Inventory Defaults")
        save_btn.setMaximumWidth(200)
        save_btn.clicked.connect(self._save_inventory)
        layout.addWidget(save_btn, alignment=Qt.AlignmentFlag.AlignRight)

        w.setLayout(layout)
        return w

    # ------------------------------------------------------------------
    # Purchase Orders Tab
    # ------------------------------------------------------------------
    def _build_po_tab(self) -> QWidget:
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        layout = QVBoxLayout(inner)

        info = QLabel(
            "Defaults applied when creating new Purchase Orders. Leave blank to "
            "fall back to company info / vendor data."
        )
        info.setStyleSheet("color: #555; font-style: italic; margin-bottom: 6px;")
        info.setWordWrap(True)
        layout.addWidget(info)

        # ── Default Addresses ──
        addr_group = QGroupBox("Default Ship-To / Bill-To")
        addr_form = QFormLayout()

        self._po_default_ship_to = QTextEdit()
        self._po_default_ship_to.setPlaceholderText(
            "Default shipping destination on new POs (leave blank to derive from "
            "linked SO customer or company info)"
        )
        self._po_default_ship_to.setMaximumHeight(80)
        addr_form.addRow("Default Ship-To:", self._po_default_ship_to)

        self._po_default_bill_to = QTextEdit()
        self._po_default_bill_to.setPlaceholderText(
            "Default billing entity on new POs (leave blank to use company info)"
        )
        self._po_default_bill_to.setMaximumHeight(80)
        addr_form.addRow("Default Bill-To:", self._po_default_bill_to)

        addr_group.setLayout(addr_form)
        layout.addWidget(addr_group)

        # ── Saved Addresses Library ──
        saved_group = QGroupBox("Saved Ship-To / Bill-To Addresses")
        saved_group.setToolTip(
            "Save named addresses here. They appear in a drop-down inside the PO "
            "dialog so you can fill Ship-To / Bill-To with one click."
        )
        saved_v = QVBoxLayout()
        saved_info = QLabel(
            "Named addresses available in the PO dialog's ▾ address picker."
        )
        saved_info.setStyleSheet("color: #555; font-style: italic;")
        saved_v.addWidget(saved_info)

        self._saved_addr_list = QListWidget()
        self._saved_addr_list.setMaximumHeight(120)
        self._saved_addr_list.setToolTip("Double-click to edit")
        self._saved_addr_list.itemDoubleClicked.connect(self._edit_saved_address)
        saved_v.addWidget(self._saved_addr_list)

        btn_row = QHBoxLayout()
        add_addr_btn = QPushButton("+ Add Address")
        add_addr_btn.clicked.connect(self._add_saved_address)
        btn_row.addWidget(add_addr_btn)
        edit_addr_btn = QPushButton("Edit")
        edit_addr_btn.clicked.connect(self._edit_saved_address)
        btn_row.addWidget(edit_addr_btn)
        rm_addr_btn = QPushButton("Remove")
        rm_addr_btn.clicked.connect(self._remove_saved_address)
        btn_row.addWidget(rm_addr_btn)
        btn_row.addStretch()
        saved_v.addLayout(btn_row)

        saved_group.setLayout(saved_v)
        layout.addWidget(saved_group)
        hdr_group = QGroupBox("PO Header Defaults")
        hdr_form = QFormLayout()

        self._po_default_terms = QComboBox()
        self._po_default_terms.addItems([
            "COD", "Due on Receipt", "Net 15", "Net 30", "Net 45", "Net 60",
        ])
        hdr_form.addRow("Default Vendor Terms:", self._po_default_terms)

        self._po_default_lead_time = QSpinBox()
        self._po_default_lead_time.setRange(0, 365)
        self._po_default_lead_time.setSuffix(" days")
        self._po_default_lead_time.setToolTip(
            "Adds N days to today for the Expected Date on new POs."
        )
        hdr_form.addRow("Default Lead Time:", self._po_default_lead_time)

        self._po_default_shipping = QDoubleSpinBox()
        self._po_default_shipping.setRange(0, 10000)
        self._po_default_shipping.setDecimals(2)
        self._po_default_shipping.setPrefix("$ ")
        hdr_form.addRow("Default Shipping Cost:", self._po_default_shipping)

        self._po_default_tax_rate = QDoubleSpinBox()
        self._po_default_tax_rate.setRange(0, 20)
        self._po_default_tax_rate.setDecimals(3)
        self._po_default_tax_rate.setSuffix(" %")
        hdr_form.addRow("Default PO Tax Rate:", self._po_default_tax_rate)

        self._po_number_prefix = QLineEdit()
        self._po_number_prefix.setPlaceholderText("PO-")
        self._po_number_prefix.setMaximumWidth(120)
        hdr_form.addRow("PO Number Prefix:", self._po_number_prefix)

        self._po_default_notes = QTextEdit()
        self._po_default_notes.setPlaceholderText(
            "Boilerplate notes auto-added to new POs (e.g., shipping instructions)"
        )
        self._po_default_notes.setMaximumHeight(60)
        hdr_form.addRow("Default Notes:", self._po_default_notes)

        hdr_group.setLayout(hdr_form)
        layout.addWidget(hdr_group)

        # ── Sender / Send Method ──
        send_group = QGroupBox("Send Defaults")
        send_form = QFormLayout()

        self._po_sender_name = QLineEdit()
        self._po_sender_name.setPlaceholderText("Buyer name (recorded as 'Sent By')")
        send_form.addRow("Default Sender Name:", self._po_sender_name)

        self._po_sender_email = QLineEdit()
        self._po_sender_email.setPlaceholderText("buyer@example.com")
        send_form.addRow("Default Sender Email:", self._po_sender_email)

        self._po_send_method = QComboBox()
        self._po_send_method.addItems(["manual", "email", "pdf"])
        send_form.addRow("Default Send Method:", self._po_send_method)

        send_group.setLayout(send_form)
        layout.addWidget(send_group)

        # ── Workflow / Auto-Restock ──
        wf_group = QGroupBox("Workflow")
        wf_form = QFormLayout()

        self._po_auto_merge = QComboBox()
        self._po_auto_merge.addItems(["ask", "always", "never"])
        self._po_auto_merge.setToolTip(
            "When creating a PO from an SO and an open draft for that vendor exists, "
            "should we merge into it?"
        )
        wf_form.addRow("Auto-Merge into Draft:", self._po_auto_merge)

        self._po_restock_multiplier = QDoubleSpinBox()
        self._po_restock_multiplier.setRange(1.0, 10.0)
        self._po_restock_multiplier.setDecimals(2)
        self._po_restock_multiplier.setSingleStep(0.25)
        self._po_restock_multiplier.setToolTip(
            "Auto-Restock orders this multiple of (min_qty - on_hand)."
        )
        wf_form.addRow("Auto-Restock Multiplier:", self._po_restock_multiplier)

        self._po_top_n_priority = QSpinBox()
        self._po_top_n_priority.setRange(0, 20)
        self._po_top_n_priority.setToolTip(
            "Highlight the top N highest-priority POs in the PO list."
        )
        wf_form.addRow("Highlight Top N Priority:", self._po_top_n_priority)

        self._po_focus_high_value = QDoubleSpinBox()
        self._po_focus_high_value.setRange(0, 1_000_000)
        self._po_focus_high_value.setDecimals(0)
        self._po_focus_high_value.setPrefix("$ ")
        self._po_focus_high_value.setToolTip(
            "Focus Mode includes POs whose total exceeds this amount."
        )
        wf_form.addRow("Focus Mode High-Value:", self._po_focus_high_value)

        self._po_cash_at_risk_warn = QDoubleSpinBox()
        self._po_cash_at_risk_warn.setRange(0, 10_000_000)
        self._po_cash_at_risk_warn.setDecimals(0)
        self._po_cash_at_risk_warn.setPrefix("$ ")
        self._po_cash_at_risk_warn.setToolTip(
            "Cash-at-Risk per vendor at or above this amount is highlighted."
        )
        wf_form.addRow("Cash-at-Risk Warning:", self._po_cash_at_risk_warn)

        wf_group.setLayout(wf_form)
        layout.addWidget(wf_group)

        layout.addStretch()

        save_btn = QPushButton("Save PO Defaults")
        save_btn.setMaximumWidth(220)
        save_btn.clicked.connect(self._save_po)
        layout.addWidget(save_btn, alignment=Qt.AlignmentFlag.AlignRight)

        scroll.setWidget(inner)
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        return w

    # ------------------------------------------------------------------
    # Email / SMTP Tab
    # ------------------------------------------------------------------
    def _build_email_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout()

        info = QLabel(
            "Configure SMTP to send quotes, invoices, sales orders, and tracking\n"
            "notifications via email directly from the app."
        )
        info.setStyleSheet("color: #555; font-style: italic; margin-bottom: 8px;")
        layout.addWidget(info)

        # ── SMTP Server ──
        server_group = QGroupBox("SMTP Server")
        sf = QFormLayout()

        self._smtp_host = QLineEdit()
        self._smtp_host.setPlaceholderText("e.g. smtp.gmail.com or smtp.office365.com")
        sf.addRow("SMTP Host:", self._smtp_host)

        self._smtp_port = QSpinBox()
        self._smtp_port.setRange(1, 65535)
        self._smtp_port.setValue(587)
        sf.addRow("Port:", self._smtp_port)

        self._smtp_tls = QCheckBox("Use TLS (recommended)")
        self._smtp_tls.setChecked(True)
        sf.addRow("", self._smtp_tls)

        server_group.setLayout(sf)
        layout.addWidget(server_group)

        # ── Credentials ──
        cred_group = QGroupBox("Login Credentials")
        cf = QFormLayout()

        self._smtp_user = QLineEdit()
        self._smtp_user.setPlaceholderText("email@yourdomain.com")
        cf.addRow("Username / Email:", self._smtp_user)

        self._smtp_password = QLineEdit()
        self._smtp_password.setPlaceholderText("App password or SMTP password")
        self._smtp_password.setEchoMode(QLineEdit.EchoMode.Password)
        cf.addRow("Password:", self._smtp_password)

        self._smtp_from = QLineEdit()
        self._smtp_from.setPlaceholderText("Sender address (defaults to username)")
        cf.addRow("From Address:", self._smtp_from)

        cred_group.setLayout(cf)
        layout.addWidget(cred_group)

        gmail_hint = QLabel(
            "Gmail users: use a Google App Password (16 characters), not your regular account password.\n"
            "Recommended Gmail settings: Host smtp.gmail.com, Port 587, TLS enabled."
        )
        gmail_hint.setWordWrap(True)
        gmail_hint.setStyleSheet(
            "color: #92400e; background: #fffbeb; border: 1px solid #fde68a; "
            "border-radius: 4px; padding: 8px;"
        )
        layout.addWidget(gmail_hint)

        # ── Buttons ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        test_btn = QPushButton("📧 Send Test Email")
        test_btn.setStyleSheet(
            "QPushButton { background: #2563eb; color: white; font-weight: bold; "
            "padding: 8px 16px; border: none; border-radius: 4px; }"
            "QPushButton:hover { background: #1d4ed8; }"
        )
        test_btn.clicked.connect(self._test_email)
        btn_row.addWidget(test_btn)

        save_btn = QPushButton("Save Email Settings")
        save_btn.setMaximumWidth(200)
        save_btn.clicked.connect(self._save_email)
        btn_row.addWidget(save_btn)

        layout.addLayout(btn_row)

        # Status
        self._email_status = QLabel("")
        layout.addWidget(self._email_status)

        layout.addStretch()
        w.setLayout(layout)
        return w

    # ------------------------------------------------------------------
    # QBO Tab
    # ------------------------------------------------------------------
    def _build_qbo_tab(self) -> QWidget:
        # Wrap in QScrollArea so long QBO content (connection + sync +
        # advanced + account-mapping) scrolls vertically on small windows.
        outer_w = QWidget()
        outer_wrap = QVBoxLayout(outer_w)
        outer_wrap.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer_wrap.addWidget(scroll)

        w = QWidget()
        outer = QVBoxLayout()

        # ── Environment banner ──────────────────────────────────────
        # Always show whether we're talking to live QBO, sandbox, or
        # running in mock mode. Updated by `_refresh_qbo_env_banner`
        # whenever connection state changes.
        self._qbo_env_banner = QLabel("")
        self._qbo_env_banner.setWordWrap(True)
        self._qbo_env_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self._qbo_env_banner)

        # ── Restructure pointer banner ──────────────────────────────
        # Per the QBO UI restructure plan: Settings is for *configuration
        # only*. Daily push/pull lives in ACCOUNTING → QBO Sync Center.
        # Compare/repair lives in ACCOUNTING → QBO Reconciliation. The
        # legacy operational buttons below remain available (collapsed
        # by default) for compatibility while users transition.
        relocate_banner = QLabel(
            "ℹ  <b>Settings = configuration only.</b> "
            "Daily push/pull → <b>Accounting → QBO Sync Center</b>. "
            "Compare/repair → <b>Accounting → QBO Reconciliation</b>. "
            "<i>Legacy sync buttons remain below (collapsed) for compatibility.</i>"
        )
        relocate_banner.setTextFormat(Qt.TextFormat.RichText)
        relocate_banner.setWordWrap(True)
        relocate_banner.setStyleSheet(
            "background-color: #e3f2fd; border: 1px solid #90caf9; "
            "border-radius: 4px; padding: 8px; color: #0d47a1; "
            "font-size: 11px;"
        )
        outer.addWidget(relocate_banner)

        # ── Connection group (top) ──────────────────────────────────
        conn_group = QGroupBox("QuickBooks Online Connection")
        conn_form = QFormLayout()

        self._qbo_conn_dot = QLabel("●")
        self._qbo_conn_dot.setStyleSheet("color: gray; font-size: 16px;")
        self._qbo_conn_text = QLabel("Status unknown")
        dot_row = QHBoxLayout()
        dot_row.addWidget(self._qbo_conn_dot)
        dot_row.addWidget(self._qbo_conn_text)
        dot_row.addStretch()
        dot_holder = QWidget()
        dot_holder.setLayout(dot_row)
        conn_form.addRow("Status:", dot_holder)

        self._qbo_conn_realm = QLabel("—")
        conn_form.addRow("Company (Realm) ID:", self._qbo_conn_realm)

        self._qbo_conn_env = QLabel("—")
        conn_form.addRow("Environment:", self._qbo_conn_env)

        self._qbo_conn_modes = QLabel("—")
        conn_form.addRow("Mode:", self._qbo_conn_modes)

        conn_btn_row = QHBoxLayout()
        self._qbo_connect_btn = QPushButton("Connect…")
        self._qbo_connect_btn.clicked.connect(self._on_qbo_connect)
        self._qbo_disconnect_btn = QPushButton("Disconnect")
        self._qbo_disconnect_btn.clicked.connect(self._on_qbo_disconnect)
        self._qbo_conn_test_btn = QPushButton("Test Connection")
        self._qbo_conn_test_btn.clicked.connect(self._test_qbo_connection)
        self._qbo_refresh_btn = QPushButton("Refresh Status")
        self._qbo_refresh_btn.clicked.connect(self._refresh_qbo_connection_status)
        # Sync Authority
        self._qbo_sync_authority = QComboBox()
        self._qbo_sync_authority.addItems(["Local Master", "QBO Master", "Hybrid"])
        self._qbo_sync_authority.setToolTip(
            "Local Master (recommended): this app owns products, inventory, and pricing.\n"
            "QBO receives accounting copies only. Prevents accidental overwrites.\n\n"
            "QBO Master: QBO is authoritative — risky in early development.\n"
            "Hybrid: manual case-by-case control."
        )
        auth_hint = QLabel("<i>Local Master: your app owns inventory & pricing. QBO receives accounting entries.</i>")
        auth_hint.setWordWrap(True)
        auth_hint.setStyleSheet("color: #555;")
        auth_row = QHBoxLayout()
        auth_row.addWidget(self._qbo_sync_authority)
        auth_row.addWidget(auth_hint, 1)
        conn_form.addRow("Sync Authority:", auth_row)

        conn_btn_row = QHBoxLayout()
        conn_btn_row.addWidget(self._qbo_connect_btn)
        conn_btn_row.addWidget(self._qbo_disconnect_btn)
        conn_btn_row.addWidget(self._qbo_conn_test_btn)
        conn_btn_row.addWidget(self._qbo_refresh_btn)
        conn_btn_row.addStretch()
        conn_form.addRow("", conn_btn_row)

        conn_group.setLayout(conn_form)
        outer.addWidget(conn_group)

        # ── Sync sections ─────────────────────────────────────────────
        # Restructure: replace a single flat wall of buttons with 3
        # visually-distinct tiers (Daily, Accounting/Advanced, Reference
        # Data). Buttons are color-coded by direction:
        #   GREEN  → push to QBO  (App → QBO)
        #   BLUE   → import from QBO (QBO → App)
        #   ORANGE → dangerous overwrite (e.g. Push Inventory Qty)

        # 1) Daily Operations — collapsed by default per restructure
        # plan. These actions properly live in QBO Sync Center; they
        # remain here only for legacy compatibility / power-user access.
        daily_header, daily_body = self._make_collapsible_section(
            "Daily Operations (legacy — prefer QBO Sync Center)",
            "Routine sync used 95% of the time. New home: ACCOUNTING → "
            "QBO Sync Center. These controls remain here for backward "
            "compatibility.",
            start_open=False,
        )
        outer.addWidget(daily_header)
        daily_layout = QVBoxLayout()
        daily_hint = QLabel(
            "Routine sync used 95% of the time. "
            "<span style='color:#1b5e20;'>Green = push to QBO</span> · "
            "<span style='color:#0d47a1;'>Blue = import from QBO</span> · "
            "<span style='color:#e65100;'>Orange = overwrites QBO data</span>."
        )
        daily_hint.setWordWrap(True)
        daily_hint.setStyleSheet("color: #555; font-style: italic;")
        daily_layout.addWidget(daily_hint)

        # Push row (green)
        daily_push_lbl = QLabel("<b>Push →</b>")
        daily_layout.addWidget(daily_push_lbl)
        daily_push = QHBoxLayout()
        self._qbo_sync_customers_btn = QPushButton("Sync Customers →QBO")
        self._qbo_sync_customers_btn.setToolTip(
            "Push local customer records (name, address, terms, balance) to QBO."
        )
        self._qbo_sync_customers_btn.clicked.connect(self._on_sync_customers)

        self._qbo_sync_vendors_btn = QPushButton("Sync Vendors →QBO")
        self._qbo_sync_vendors_btn.setToolTip(
            "Push local vendor records (name, address, phone, terms) to QBO."
        )
        self._qbo_sync_vendors_btn.clicked.connect(self._on_sync_vendors)

        self._qbo_sync_products_btn = QPushButton("Sync Products →QBO")
        self._qbo_sync_products_btn.setToolTip(
            "Push your product catalog to QBO as Items/Services.\n"
            "Item type mapping (inventoried → Inventory, service → Service) is applied."
        )
        self._qbo_sync_products_btn.clicked.connect(self._on_sync_products)

        self._qbo_sync_invoices_btn = QPushButton("Sync Invoices →QBO")
        self._qbo_sync_invoices_btn.setToolTip(
            "Push eligible (unexported, non-void) local invoices to QBO."
        )
        self._qbo_sync_invoices_btn.clicked.connect(self._on_sync_invoices)

        for b in (
            self._qbo_sync_customers_btn, self._qbo_sync_vendors_btn,
            self._qbo_sync_products_btn, self._qbo_sync_invoices_btn,
        ):
            self._style_qbo_btn(b, "push")
            daily_push.addWidget(b)
        daily_push.addStretch()
        daily_layout.addLayout(daily_push)

        # Dangerous overwrite — its own emphasized row (orange)
        danger_row = QHBoxLayout()
        self._qbo_push_qty_btn = QPushButton(
            "⚠  Push Inventory Qty →QBO  (overwrites QBO qty_on_hand)"
        )
        self._qbo_push_qty_btn.setToolTip(
            "Send current qty_on_hand as QBO inventory adjustments.\n"
            "Requires Inventory Asset account mapping to be set."
        )
        self._qbo_push_qty_btn.clicked.connect(self._on_push_inventory_qty)
        self._style_qbo_btn(self._qbo_push_qty_btn, "danger")
        danger_row.addWidget(self._qbo_push_qty_btn)
        danger_row.addStretch()
        daily_layout.addLayout(danger_row)

        # Import row (blue)
        daily_imp_lbl = QLabel("<b>Import ←</b>")
        daily_layout.addWidget(daily_imp_lbl)
        daily_imp = QHBoxLayout()
        self._qbo_import_customers_btn = QPushButton("Import Customers ←QBO")
        self._qbo_import_customers_btn.setToolTip(
            "Pull customers from QBO, linking by name + stamping balance / last-pulled."
        )
        self._qbo_import_customers_btn.clicked.connect(self._on_import_customers)

        self._qbo_import_vendors_btn = QPushButton("Import Vendors ←QBO")
        self._qbo_import_vendors_btn.setToolTip(
            "Pull vendors from QBO, linking by name and stamping balance + last-pulled timestamp."
        )
        self._qbo_import_vendors_btn.clicked.connect(self._on_import_vendors)

        self._qbo_import_products_btn = QPushButton("Import Products ←QBO")
        self._qbo_import_products_btn.setToolTip(
            "Pull QBO Items to cross-reference with local SKUs.\n"
            "Does not overwrite your product data."
        )
        self._qbo_import_products_btn.clicked.connect(self._on_import_products)

        for b in (
            self._qbo_import_customers_btn, self._qbo_import_vendors_btn,
            self._qbo_import_products_btn,
        ):
            self._style_qbo_btn(b, "import")
            daily_imp.addWidget(b)
        daily_imp.addStretch()
        daily_layout.addLayout(daily_imp)

        daily_body.setLayout(daily_layout)
        outer.addWidget(daily_body)

        # 2) Accounting / Advanced — collapsible, default collapsed
        adv_header, adv_body = self._make_collapsible_section(
            "Accounting / Advanced",
            "Money-flow sync used by bookkeepers. Most operators rarely need these.",
            start_open=False,
        )
        outer.addWidget(adv_header)
        adv_layout = QVBoxLayout()

        # Pulls (blue)
        adv_pull_lbl = QLabel("<b>Import ←</b>")
        adv_layout.addWidget(adv_pull_lbl)
        adv_pull = QHBoxLayout()
        self._qbo_import_invoices_btn = QPushButton("Import Invoices ←QBO")
        self._qbo_import_invoices_btn.setToolTip(
            "Pull invoices from QBO and upsert into the local invoices table. "
            "Existing locally-authored invoices are matched by QBO id or invoice number; "
            "new ones are inserted with source = 'qbo'."
        )
        self._qbo_import_invoices_btn.clicked.connect(self._on_import_invoices)

        self._qbo_import_payments_btn = QPushButton("Import Payments ←QBO")
        self._qbo_import_payments_btn.setToolTip(
            "Pull customer payments from QBO and reconcile the linked invoices' "
            "amount_paid / balance_due / status."
        )
        self._qbo_import_payments_btn.clicked.connect(self._on_import_payments)

        self._qbo_import_credits_btn = QPushButton("Import Credit Memos ←QBO")
        self._qbo_import_credits_btn.setToolTip(
            "Pull credit memos from QBO and upsert into customer_credits."
        )
        self._qbo_import_credits_btn.clicked.connect(self._on_import_credits)

        self._qbo_import_bills_btn = QPushButton("Import Bills ←QBO")
        self._qbo_import_bills_btn.setToolTip(
            "Pull vendor bills from QBO and update bill_balance on linked POs."
        )
        self._qbo_import_bills_btn.clicked.connect(self._on_import_bills)

        for b in (
            self._qbo_import_invoices_btn, self._qbo_import_payments_btn,
            self._qbo_import_credits_btn, self._qbo_import_bills_btn,
        ):
            self._style_qbo_btn(b, "import")
            adv_pull.addWidget(b)
        adv_pull.addStretch()
        adv_layout.addLayout(adv_pull)

        adv_pull2 = QHBoxLayout()
        self._qbo_import_pos_btn = QPushButton("Import POs ←QBO")
        self._qbo_import_pos_btn.setToolTip(
            "Pull purchase orders from QBO and reconcile linked local POs "
            "(total / status). Local POs not auto-created."
        )
        self._qbo_import_pos_btn.clicked.connect(self._on_import_pos)

        self._qbo_import_estimates_btn = QPushButton("Import Estimates ←QBO")
        self._qbo_import_estimates_btn.setToolTip(
            "Pull estimates from QBO and reconcile linked local quotes "
            "(total / status). Local quote lines preserved."
        )
        self._qbo_import_estimates_btn.clicked.connect(self._on_import_estimates)

        self._qbo_import_sales_receipts_btn = QPushButton(
            "Import Sales Receipts ←QBO"
        )
        self._qbo_import_sales_receipts_btn.setToolTip(
            "Pull cash sales (SalesReceipt) from QBO into invoices "
            "(invoice_kind='sales_receipt'). Idempotent."
        )
        self._qbo_import_sales_receipts_btn.clicked.connect(
            self._on_import_sales_receipts
        )

        self._qbo_import_refund_receipts_btn = QPushButton(
            "Import Refund Receipts ←QBO"
        )
        self._qbo_import_refund_receipts_btn.setToolTip(
            "Pull cash refunds (RefundReceipt) from QBO into "
            "customer_credits. Idempotent."
        )
        self._qbo_import_refund_receipts_btn.clicked.connect(
            self._on_import_refund_receipts
        )

        self._qbo_import_bill_payments_btn = QPushButton(
            "Import Bill Payments ←QBO"
        )
        self._qbo_import_bill_payments_btn.setToolTip(
            "Pull QBO BillPayments into bill_payments and reconcile "
            "linked PO bill_balance."
        )
        self._qbo_import_bill_payments_btn.clicked.connect(
            self._on_import_bill_payments
        )

        for b in (
            self._qbo_import_pos_btn, self._qbo_import_estimates_btn,
            self._qbo_import_sales_receipts_btn,
            self._qbo_import_refund_receipts_btn,
            self._qbo_import_bill_payments_btn,
        ):
            self._style_qbo_btn(b, "import")
            adv_pull2.addWidget(b)
        adv_pull2.addStretch()
        adv_layout.addLayout(adv_pull2)

        # Pushes (green)
        adv_push_lbl = QLabel("<b>Push →</b>")
        adv_layout.addWidget(adv_push_lbl)
        adv_push = QHBoxLayout()
        self._qbo_sync_payments_btn = QPushButton("Sync Payments →QBO")
        self._qbo_sync_payments_btn.setToolTip(
            "Push local invoice_payments rows to QBO as Payment objects."
        )
        self._qbo_sync_payments_btn.clicked.connect(self._on_sync_payments)

        self._qbo_sync_je_btn = QPushButton("Sync Journal Entries →QBO")
        self._qbo_sync_je_btn.setToolTip(
            "Post the COGS / inventory journal entry for each eligible invoice."
        )
        self._qbo_sync_je_btn.clicked.connect(self._on_sync_journal_entries)

        self._qbo_push_sales_receipts_btn = QPushButton(
            "Push Sales Receipts →QBO"
        )
        self._qbo_push_sales_receipts_btn.setToolTip(
            "Push local invoices flagged invoice_kind='sales_receipt' "
            "to QBO as SalesReceipts. Idempotent."
        )
        self._qbo_push_sales_receipts_btn.clicked.connect(
            self._on_push_sales_receipts
        )

        self._qbo_push_refunds_btn = QPushButton("Push Refunds →QBO")
        self._qbo_push_refunds_btn.setToolTip(
            "Push customer_credits flagged as cash refunds "
            "(source_type='refund') to QBO as RefundReceipts."
        )
        self._qbo_push_refunds_btn.clicked.connect(self._on_push_refunds)

        self._qbo_push_bill_payments_btn = QPushButton(
            "Push Bill Payments →QBO"
        )
        self._qbo_push_bill_payments_btn.setToolTip(
            "Push local bill_payments to QBO as BillPayments, "
            "linking to each paid Bill."
        )
        self._qbo_push_bill_payments_btn.clicked.connect(
            self._on_push_bill_payments
        )

        for b in (
            self._qbo_sync_payments_btn, self._qbo_sync_je_btn,
            self._qbo_push_sales_receipts_btn,
            self._qbo_push_refunds_btn, self._qbo_push_bill_payments_btn,
        ):
            self._style_qbo_btn(b, "push")
            adv_push.addWidget(b)
        adv_push.addStretch()
        adv_layout.addLayout(adv_push)

        adv_body.setLayout(adv_layout)
        outer.addWidget(adv_body)

        # 3) Reference Data — collapsible, default collapsed
        ref_header, ref_body = self._make_collapsible_section(
            "Reference Data (Tax Codes, Classes, Locations)",
            "Catalog mirrors used to populate dropdowns. Pull occasionally.",
            start_open=False,
        )
        outer.addWidget(ref_header)
        ref_layout = QVBoxLayout()
        ref_row = QHBoxLayout()
        self._qbo_import_tax_codes_btn = QPushButton("Import Tax Codes ←QBO")
        self._qbo_import_tax_codes_btn.setToolTip(
            "Mirror the QBO TaxCode catalog into qbo_tax_codes "
            "(used for line-level tax overrides)."
        )
        self._qbo_import_tax_codes_btn.clicked.connect(
            self._on_import_tax_codes
        )

        self._qbo_import_classes_btn = QPushButton("Import Classes ←QBO")
        self._qbo_import_classes_btn.setToolTip(
            "Mirror the QBO Class catalog into qbo_classes "
            "(cost-center / department tagging)."
        )
        self._qbo_import_classes_btn.clicked.connect(
            self._on_import_classes
        )

        self._qbo_import_locations_btn = QPushButton("Import Locations ←QBO")
        self._qbo_import_locations_btn.setToolTip(
            "Mirror the QBO Department/Location catalog into "
            "qbo_locations."
        )
        self._qbo_import_locations_btn.clicked.connect(
            self._on_import_locations
        )

        for b in (
            self._qbo_import_tax_codes_btn, self._qbo_import_classes_btn,
            self._qbo_import_locations_btn,
        ):
            self._style_qbo_btn(b, "import")
            ref_row.addWidget(b)
        ref_row.addStretch()
        ref_layout.addLayout(ref_row)
        ref_body.setLayout(ref_layout)
        outer.addWidget(ref_body)

        # ── Sync Status Dashboard ───────────────────────────────────
        status_group = QGroupBox("Sync Status")
        status_vbox = QVBoxLayout()
        from PySide6.QtWidgets import QTableWidget as _QTW, QTableWidgetItem as _QTI, QHeaderView as _QHV
        self._qbo_status_table = _QTW(0, 5)
        self._qbo_status_table.setHorizontalHeaderLabels(
            ["Entity", "Pending", "Last Sync", "Synced", "Failed"]
        )
        self._qbo_status_table.horizontalHeader().setSectionResizeMode(0, _QHV.ResizeMode.Stretch)
        for c in (1, 2, 3, 4):
            self._qbo_status_table.horizontalHeader().setSectionResizeMode(
                c, _QHV.ResizeMode.ResizeToContents)
        self._qbo_status_table.setMaximumHeight(160)
        self._qbo_status_table.setEditTriggers(_QTW.EditTrigger.NoEditTriggers)
        self._qbo_status_table.setSelectionMode(_QTW.SelectionMode.NoSelection)
        status_vbox.addWidget(self._qbo_status_table)

        status_btn_row = QHBoxLayout()
        self._qbo_view_failures_btn = QPushButton("View Failures…")
        self._qbo_view_failures_btn.setToolTip(
            "Open a window listing every failed QBO sync attempt so you "
            "can review the error message and mark issues resolved."
        )
        self._qbo_view_failures_btn.clicked.connect(self._open_qbo_failures_dialog)
        self._qbo_pending_queue_btn = QPushButton("Pending Queue…")
        self._qbo_pending_queue_btn.setToolTip(
            "Records queued for QuickBooks but not yet pushed. Open the "
            "queue to push them in bulk."
        )
        self._qbo_pending_queue_btn.clicked.connect(self._open_qbo_pending_dialog)
        self._qbo_sync_log_btn = QPushButton("Sync Log…")
        self._qbo_sync_log_btn.setToolTip(
            "Append-only history of every QBO sync attempt — success, "
            "failure, retry, and ignored — for audit and troubleshooting."
        )
        self._qbo_sync_log_btn.clicked.connect(self._open_qbo_sync_log_dialog)
        self._qbo_refresh_status_btn = QPushButton("Refresh")
        self._qbo_refresh_status_btn.clicked.connect(self._populate_qbo_status_table)
        status_btn_row.addWidget(self._qbo_view_failures_btn)
        status_btn_row.addWidget(self._qbo_pending_queue_btn)
        status_btn_row.addWidget(self._qbo_sync_log_btn)
        status_btn_row.addWidget(self._qbo_refresh_status_btn)
        status_btn_row.addStretch()
        status_vbox.addLayout(status_btn_row)

        self._populate_qbo_status_table()
        status_group.setLayout(status_vbox)
        outer.addWidget(status_group)

        # ── Advanced (existing fields) ──────────────────────────────
        adv_group = QGroupBox("Advanced — Credentials & Account Mapping")
        adv_group.setCheckable(True)
        adv_group.setChecked(True)
        layout = QVBoxLayout()
        form = QFormLayout()

        self._qbo_client_id = QLineEdit()
        self._qbo_client_id.setPlaceholderText("Enter QBO Client ID...")
        self._qbo_client_id.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Client ID:", self._qbo_client_id)

        self._qbo_client_secret = QLineEdit()
        self._qbo_client_secret.setPlaceholderText("Enter QBO Client Secret...")
        self._qbo_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Client Secret:", self._qbo_client_secret)

        self._qbo_realm_id = QLineEdit()
        self._qbo_realm_id.setPlaceholderText("Enter QBO Company ID (Realm)...")
        form.addRow("Company ID:", self._qbo_realm_id)

        self._qbo_refresh_token = QTextEdit()
        self._qbo_refresh_token.setPlaceholderText("Paste QBO Refresh Token...")
        self._qbo_refresh_token.setMaximumHeight(60)
        # Mask the token by default — render dots via a monospace font and
        # an obscuring stylesheet. A small toggle button reveals it on
        # demand. The underlying QTextEdit value is unchanged so save/load
        # logic keeps working.
        self._qbo_refresh_token._jaks_masked = True  # type: ignore[attr-defined]

        def _apply_mask():
            masked = getattr(self._qbo_refresh_token, "_jaks_masked", True)
            if masked:
                # Webkit-style password masking via CSS isn't available on
                # QTextEdit, so we substitute a custom font that renders
                # every glyph as a bullet (•). Easiest portable approach:
                # set echo via a stylesheet color trick + selectable=False.
                self._qbo_refresh_token.setStyleSheet(
                    "QTextEdit { color: transparent; "
                    "background-image: none; "
                    "selection-background-color: transparent; }"
                )
                # Overlay a label so the operator sees that *something* is
                # there without exposing the secret.
                self._qbo_refresh_token.setToolTip(
                    "Refresh token is hidden — click ‘Reveal’ to view."
                )
            else:
                self._qbo_refresh_token.setStyleSheet("")
                self._qbo_refresh_token.setToolTip("")

        self._qbo_refresh_token_apply_mask = _apply_mask
        _apply_mask()

        rt_row = QHBoxLayout()
        rt_row.addWidget(self._qbo_refresh_token, 1)
        self._qbo_refresh_token_reveal_btn = QPushButton("Reveal")
        self._qbo_refresh_token_reveal_btn.setCheckable(True)
        self._qbo_refresh_token_reveal_btn.setMaximumWidth(80)
        def _toggle_reveal(checked: bool):
            self._qbo_refresh_token._jaks_masked = not checked  # type: ignore[attr-defined]
            self._qbo_refresh_token_reveal_btn.setText("Hide" if checked else "Reveal")
            self._qbo_refresh_token_apply_mask()
        self._qbo_refresh_token_reveal_btn.toggled.connect(_toggle_reveal)
        rt_row.addWidget(self._qbo_refresh_token_reveal_btn)
        rt_wrap = QWidget()
        rt_wrap.setLayout(rt_row)
        form.addRow("Refresh Token:", rt_wrap)

        self._qbo_sandbox = QCheckBox("Use Sandbox Environment")
        self._qbo_sandbox.setChecked(True)
        form.addRow("", self._qbo_sandbox)

        self._qbo_write_enabled = QCheckBox("Enable QBO Write Operations")
        self._qbo_write_enabled.setChecked(False)
        form.addRow("", self._qbo_write_enabled)

        # QBO.9 — central mock-mode toggle (overrides auto-detection).
        self._qbo_mock_mode = QCheckBox("QBO Mock Mode (no real API calls)")
        self._qbo_mock_mode.setToolTip(
            "When checked, all QBO writes are simulated with deterministic "
            "stub responses. Turn off to allow live QBO traffic (requires "
            "credentials above AND 'Enable QBO Write Operations')."
        )
        form.addRow("", self._qbo_mock_mode)

        btn_row = QHBoxLayout()
        self._qbo_test_btn = QPushButton("Test Connection")
        self._qbo_test_btn.clicked.connect(self._test_qbo_connection)
        self._qbo_save_btn = QPushButton("Save QBO Settings")
        self._qbo_save_btn.clicked.connect(self._save_qbo_settings)
        btn_row.addWidget(self._qbo_test_btn)
        btn_row.addWidget(self._qbo_save_btn)
        btn_row.addStretch()
        form.addRow("", btn_row)

        self._qbo_status = QLabel("")
        form.addRow("Status:", self._qbo_status)

        layout.addLayout(form)

        # ── QBO Account Mapping (D2) ──
        # Seven chart-of-accounts ids QBO requires when creating Items,
        # Invoices, Bills, Payments, and Credit Memos. Empty values fall
        # back to the legacy "1"/"2"/"3" placeholders (sandbox only —
        # real QBO writes will fail unless configured).
        acct_group = QGroupBox("QBO Account Mapping")
        acct_form = QFormLayout()
        acct_hint = QLabel(
            "Enter your QuickBooks Online account IDs (numeric, from "
            "the QBO chart of accounts). These map every line we push "
            "to the right ledger. Leave blank to fall back to sandbox "
            "defaults."
        )
        acct_hint.setWordWrap(True)
        acct_hint.setStyleSheet("color: #555; font-style: italic;")
        acct_form.addRow(acct_hint)

        # Refresh button — pulls live chart of accounts from QBO and
        # populates every combo with name + id pairs.
        refresh_row = QHBoxLayout()
        self._qbo_acct_refresh_btn = QPushButton("⟳ Refresh Accounts from QBO")
        self._qbo_acct_refresh_btn.clicked.connect(self._refresh_qbo_accounts)
        self._qbo_acct_refresh_status = QLabel("")
        self._qbo_acct_refresh_status.setStyleSheet("color: #555;")
        refresh_row.addWidget(self._qbo_acct_refresh_btn)
        refresh_row.addWidget(self._qbo_acct_refresh_status, 1)
        acct_form.addRow(refresh_row)

        # Per-field type filters (used by _refresh_qbo_accounts).
        self._qbo_acct_filters: dict[str, tuple[str, ...]] = {
            "inventory_asset":  ("Other Current Asset",),
            "sales_income":     ("Income",),
            "cogs":             ("Cost of Goods Sold",),
            "core_liability":   ("Other Current Liability", "Long Term Liability"),
            "ar":               ("Accounts Receivable",),
            "ap":               ("Accounts Payable",),
            "tax_payable":      ("Other Current Liability",),
            "freight_income":   ("Income",),
            "freight_expense":  ("Expense", "Cost of Goods Sold"),
            "sales_discounts":  ("Income",),
            "sales_returns":    ("Income",),
            "warranty_reserve": ("Expense", "Other Current Liability"),
        }

        def _mk_acct_combo(placeholder: str) -> QComboBox:
            cb = QComboBox()
            cb.setEditable(True)
            cb.setInsertPolicy(QComboBox.NoInsert)
            cb.lineEdit().setPlaceholderText(placeholder)
            return cb

        self._qbo_acct_inventory = _mk_acct_combo("Inventory Asset account ID")
        acct_form.addRow("Inventory Asset:", self._qbo_acct_inventory)

        self._qbo_acct_income = _mk_acct_combo("Sales Income account ID")
        acct_form.addRow("Sales Income:", self._qbo_acct_income)

        self._qbo_acct_cogs = _mk_acct_combo("Cost of Goods Sold account ID")
        acct_form.addRow("COGS:", self._qbo_acct_cogs)

        self._qbo_acct_core = _mk_acct_combo("Core Liability account ID")
        acct_form.addRow("Core Liability:", self._qbo_acct_core)

        self._qbo_acct_ar = _mk_acct_combo("Accounts Receivable ID")
        acct_form.addRow("Accounts Receivable:", self._qbo_acct_ar)

        self._qbo_acct_ap = _mk_acct_combo("Accounts Payable ID")
        acct_form.addRow("Accounts Payable:", self._qbo_acct_ap)

        self._qbo_acct_tax = _mk_acct_combo("Sales Tax Payable account ID")
        acct_form.addRow("Tax Payable:", self._qbo_acct_tax)

        ext_sep = QLabel("<b>Extended Accounts</b> — for freight, discounts, returns, and warranty:")
        ext_sep.setStyleSheet("color: #3d4a38; margin-top: 8px;")
        acct_form.addRow(ext_sep)

        self._qbo_acct_freight_income = _mk_acct_combo("Freight Revenue account ID")
        acct_form.addRow("Freight Income:", self._qbo_acct_freight_income)

        self._qbo_acct_freight_expense = _mk_acct_combo("Shipping Expense account ID")
        acct_form.addRow("Freight Expense:", self._qbo_acct_freight_expense)

        self._qbo_acct_discounts = _mk_acct_combo("Sales Discounts account ID")
        acct_form.addRow("Sales Discounts:", self._qbo_acct_discounts)

        self._qbo_acct_returns = _mk_acct_combo("Sales Returns account ID")
        acct_form.addRow("Sales Returns:", self._qbo_acct_returns)

        self._qbo_acct_warranty = _mk_acct_combo("Warranty Reserve account ID")
        acct_form.addRow("Warranty Reserve:", self._qbo_acct_warranty)

        acct_btn_row = QHBoxLayout()
        save_acct_btn = QPushButton("Save Account Mapping")
        save_acct_btn.clicked.connect(self._save_qbo_account_mapping)
        acct_btn_row.addStretch()
        acct_btn_row.addWidget(save_acct_btn)
        acct_form.addRow("", acct_btn_row)

        acct_group.setLayout(acct_form)
        layout.addWidget(acct_group)

        # ------------------------------------------------------------------
        # Tier 3 — Tax / Currency / Webhook
        # ------------------------------------------------------------------
        tc_group = QGroupBox("Tax, Currency & Webhook (Tier 3)")
        tc_form = QFormLayout()

        self._qbo_default_tax_code = QLineEdit()
        self._qbo_default_tax_code.setPlaceholderText(
            "QBO TaxCode Id, e.g. 4 (NON) or 3 (TAX) — applied to lines without an override"
        )
        tc_form.addRow("Default TaxCode Id:", self._qbo_default_tax_code)

        self._qbo_default_currency = QLineEdit()
        self._qbo_default_currency.setPlaceholderText(
            "ISO 4217, e.g. USD / CAD / EUR — leave blank to use realm home currency"
        )
        self._qbo_default_currency.setMaxLength(3)
        tc_form.addRow("Default Currency:", self._qbo_default_currency)

        self._qbo_webhook_token = QLineEdit()
        self._qbo_webhook_token.setEchoMode(QLineEdit.EchoMode.Password)
        self._qbo_webhook_token.setPlaceholderText(
            "Intuit Verifier Token (Developer dashboard → Webhooks) — empty = endpoint disabled"
        )
        tc_form.addRow("Webhook Verifier Token:", self._qbo_webhook_token)

        tc_hint = QLabel(
            "POST endpoint: <code>/qbo/webhook</code>. The token must match "
            "the value configured in your Intuit Developer dashboard so that "
            "we can verify the <code>intuit-signature</code> header. Without a "
            "token, the endpoint rejects all traffic (fail-closed)."
        )
        tc_hint.setWordWrap(True)
        tc_hint.setStyleSheet("color: #555; font-style: italic;")
        tc_form.addRow("", tc_hint)

        tc_btn_row = QHBoxLayout()
        save_tc_btn = QPushButton("Save Tax / Currency / Webhook")
        save_tc_btn.clicked.connect(self._save_qbo_tier3_settings)
        tc_btn_row.addStretch()
        tc_btn_row.addWidget(save_tc_btn)
        tc_form.addRow("", tc_btn_row)

        tc_group.setLayout(tc_form)
        layout.addWidget(tc_group)

        layout.addStretch()
        adv_group.setLayout(layout)
        outer.addWidget(adv_group)

        outer.addStretch()
        w.setLayout(outer)
        scroll.setWidget(w)
        return outer_w

    # ------------------------------------------------------------------
    # Scraper Tab
    # ------------------------------------------------------------------
    def _build_scraper_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout()

        # PAI credentials
        pai_group = QGroupBox("PAI Industries Credentials")
        pai_form = QFormLayout()

        self._pai_username = QLineEdit()
        self._pai_username.setPlaceholderText("PAI store username")
        pai_form.addRow("PAI Username:", self._pai_username)

        self._pai_password = QLineEdit()
        self._pai_password.setPlaceholderText("PAI store password")
        self._pai_password.setEchoMode(QLineEdit.EchoMode.Password)
        pai_form.addRow("PAI Password:", self._pai_password)

        self._pai_base_url = QLineEdit()
        self._pai_base_url.setPlaceholderText("https://store.paiindustries.com")
        pai_form.addRow("PAI Base URL:", self._pai_base_url)

        pai_group.setLayout(pai_form)
        layout.addWidget(pai_group)

        # Scraper general settings
        scraper_group = QGroupBox("Scraper Settings")
        scraper_form = QFormLayout()

        self._scrape_delay = QDoubleSpinBox()
        self._scrape_delay.setRange(0.5, 10.0)
        self._scrape_delay.setDecimals(1)
        self._scrape_delay.setSuffix(" sec")
        self._scrape_delay.setValue(2.0)
        scraper_form.addRow("Request Delay:", self._scrape_delay)

        scraper_group.setLayout(scraper_form)
        layout.addWidget(scraper_group)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        test_btn = QPushButton("🔐 Test PAI Login")
        test_btn.setToolTip(
            "Open a real Chrome window with your saved credentials pre-filled.\n"
            "Use this to verify creds, solve a CAPTCHA, or complete 2FA.\n"
            "The login session is then reused by headless lookups."
        )
        test_btn.setMaximumWidth(200)
        test_btn.clicked.connect(self._test_pai_login)
        btn_row.addWidget(test_btn)

        save_btn = QPushButton("Save Scraper Settings")
        save_btn.setMaximumWidth(200)
        save_btn.clicked.connect(self._save_scraper)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        w.setLayout(layout)
        return w

    # ------------------------------------------------------------------
    # System Tab
    # ------------------------------------------------------------------
    def _build_system_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout()
        form = QFormLayout()

        from db.database import get_database_url
        db_url = get_database_url()
        db_type = "PostgreSQL" if "postgresql" in db_url else "SQLite"

        form.addRow("Database:", QLabel(db_type))
        db_display = db_url[:60] + "..." if len(db_url) > 60 else db_url
        lbl = QLabel(db_display)
        lbl.setStyleSheet("color: gray;")
        form.addRow("Location:", lbl)
        form.addRow("App Version:", QLabel("2.0.0"))
        form.addRow("Config File:", QLabel(str(self._get_config_path())))

        layout.addLayout(form)

        btn_row = QHBoxLayout()
        backup_btn = QPushButton("Backup Database")
        backup_btn.clicked.connect(self._backup_database)
        btn_row.addWidget(backup_btn)

        open_logs_btn = QPushButton("📁 Open Log Folder")
        open_logs_btn.setToolTip(
            "Open the folder containing jaks_inventory.log for support / UAT feedback."
        )
        open_logs_btn.clicked.connect(self._open_log_folder)
        btn_row.addWidget(open_logs_btn)

        reset_demo_btn = QPushButton("🔄 Reset to Demo Data")
        reset_demo_btn.setToolTip(
            "Wipe all data and re-seed with a fresh demo set.\n"
            "Settings and QBO credentials are preserved.\n"
            "A two-step confirmation is required."
        )
        reset_demo_btn.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: white; font-weight: bold; }"
        )
        reset_demo_btn.clicked.connect(self._reset_to_demo_data)
        btn_row.addWidget(reset_demo_btn)

        sandbox_btn = QPushButton("🧪 Load Sandbox Data")
        sandbox_btn.setToolTip(
            "Seed the database with 4 vendors, 4 customers, and 15 test products\n"
            "for the full sales flow test (Quote → SO → PO → Invoice).\n"
            "Skips records that already exist — safe to run multiple times."
        )
        sandbox_btn.setStyleSheet(
            "QPushButton { background-color: #1565c0; color: white; font-weight: bold; }"
        )
        sandbox_btn.clicked.connect(self._load_sandbox_data)
        btn_row.addWidget(sandbox_btn)

        reset_btn = QPushButton("Reset All Data")
        reset_btn.setStyleSheet(
            "QPushButton { background-color: #8B0000; color: white; font-weight: bold; }"
        )
        reset_btn.clicked.connect(self._reset_all_data)
        btn_row.addWidget(reset_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addStretch()
        w.setLayout(layout)
        return w

    # ------------------------------------------------------------------
    # Appearance Tab (theme + density)
    # ------------------------------------------------------------------
    def _build_appearance_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        form = QFormLayout()

        from .theme import current_theme

        self._theme_combo = QComboBox()
        self._theme_combo.addItem("Light (default)", "light")
        self._theme_combo.addItem("Dark", "dark")
        idx = self._theme_combo.findData(current_theme())
        if idx >= 0:
            self._theme_combo.setCurrentIndex(idx)
        form.addRow("Theme:", self._theme_combo)

        hint = QLabel(
            "Changing the theme takes effect immediately. "
            "Some dialogs that are already open may need to be reopened "
            "to fully restyle."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-style: italic;")
        form.addRow("", hint)

        layout.addLayout(form)
        layout.addStretch()

        save_btn = QPushButton("Apply Theme")
        save_btn.setMaximumWidth(180)
        save_btn.clicked.connect(self._save_appearance)
        layout.addWidget(save_btn, alignment=Qt.AlignmentFlag.AlignRight)
        return w

    def _save_appearance(self) -> None:
        from PySide6.QtWidgets import QApplication
        from .theme import apply_theme, set_theme_setting

        theme = self._theme_combo.currentData() or "light"
        try:
            set_theme_setting(theme)
            app = QApplication.instance()
            if app is not None:
                apply_theme(app, theme)
        except Exception as exc:
            QMessageBox.warning(self, "Theme", f"Failed to apply theme: {exc}")
            return
        QMessageBox.information(
            self, "Theme Applied",
            f"Theme set to {theme.title()}. "
            "Reopen any open dialogs for a complete restyle.",
        )

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------
    def _get_config_path(self) -> Path:
        return Path(__file__).parent.parent.parent.parent / ".env"

    def _load_settings(self) -> None:
        """Load settings from DB + environment."""
        # Company tab from DB
        self._company_name.setText(get_setting("company_name", "JAK'S Diesel"))
        self._company_address.setText(get_setting("company_address", ""))
        self._company_city.setText(get_setting("company_city", "Henderson"))
        self._company_state.setText(get_setting("company_state", "NV"))
        self._company_zip.setText(get_setting("company_zip", ""))
        self._company_phone.setText(get_setting("company_phone", ""))
        self._company_email.setText(get_setting("company_email", ""))
        self._company_website.setText(get_setting("company_website", ""))
        logo = get_setting("company_logo_path", "assets/logo.png")
        self._logo_path.setText(logo)
        self._update_logo_preview()

        # Invoice tab from DB
        self._tax_rate.setValue(float(get_setting("default_tax_rate", "0")))
        self._tax_home_state.setText(get_setting("tax_home_state", ""))
        self._tax_ship_from_zip.setText(get_setting("tax_ship_from_zip", ""))
        self._tax_destination_sourced.setChecked(get_setting("tax_destination_sourced", "1") == "1")
        # TaxJar provider settings
        provider = (get_setting("tax_provider", "manual") or "manual").lower()
        idx_p = self._tax_provider.findText(provider)
        if idx_p >= 0:
            self._tax_provider.setCurrentIndex(idx_p)
        self._taxjar_api_key.setText(get_setting("taxjar_api_key", ""))
        self._taxjar_sandbox.setChecked(get_setting("taxjar_sandbox", "0") == "1")
        terms = get_setting("payment_terms", "Net 30")
        idx = self._payment_terms.findText(terms)
        if idx >= 0:
            self._payment_terms.setCurrentIndex(idx)
        ct = get_setting("default_customer_terms", "COD")
        idx2 = self._default_customer_terms.findText(ct)
        if idx2 >= 0:
            self._default_customer_terms.setCurrentIndex(idx2)
        self._invoice_prefix.setText(get_setting("invoice_prefix", "INV-"))
        self._invoice_notes.setPlainText(get_setting("invoice_notes", "Thank you for your business!"))
        self._show_core_charges.setChecked(get_setting("show_core_charges", "1") == "1")

        # Quote display settings
        self._quote_show_sku.setChecked(get_setting("quote_show_sku", "0") == "1")
        self._quote_show_warranty.setChecked(get_setting("quote_show_warranty", "1") == "1")
        self._quote_show_core.setChecked(get_setting("quote_show_core", "1") == "1")
        self._quote_show_notes.setChecked(get_setting("quote_show_notes", "1") == "1")
        self._quote_show_freight.setChecked(get_setting("quote_show_freight", "1") == "1")
        self._quote_show_company_address.setChecked(
            get_setting("quote_show_company_address", "1") == "1")
        self._quote_show_supplier.setChecked(
            get_setting("quote_show_supplier", "0") == "1")
        try:
            self._quote_logo_size_pct.setValue(int(get_setting("quote_logo_size_pct", "100")))
        except Exception:
            self._quote_logo_size_pct.setValue(100)
        self._quote_default_terms.setText(get_setting("quote_default_terms", ""))
        try:
            self._quote_default_expires_days.setValue(
                int(get_setting("quote_default_expires_days", "30")))
        except Exception:
            self._quote_default_expires_days.setValue(30)
        self._business_hq_zip.setText(get_setting("business_hq_zip", "82001") or "82001")

        method = get_setting("default_shipping_method", "Will Call") or "Will Call"
        idx = self._default_ship_method.findText(method)
        if idx >= 0:
            self._default_ship_method.setCurrentIndex(idx)
        else:
            self._default_ship_method.setEditText(method)
        try:
            self._default_ship_fee.setValue(float(get_setting("default_shipping_fee", "0") or 0))
        except (TypeError, ValueError):
            self._default_ship_fee.setValue(0.0)
        self._default_ship_taxable.setChecked(
            get_setting("default_shipping_taxable", "1") == "1"
        )

        # Inventory tab from DB
        self._sku_prefix.setText(get_setting("sku_prefix", ""))
        self._low_stock_threshold.setValue(int(get_setting("low_stock_threshold", "5")))
        formula = get_setting("reorder_formula", "max_minus_onhand")
        self._reorder_formula.setCurrentIndex(0 if formula == "max_minus_onhand" else 1)
        self._auto_barcode.setChecked(get_setting("auto_barcode", "1") == "1")
        self._so_manual_po_mode.setChecked(get_setting("so_manual_po_mode", "1") == "1")
        self._velocity_lookback.setValue(int(get_setting("velocity_lookback_days", "90")))
        self._safety_stock_mult.setValue(float(get_setting("safety_stock_multiplier", "1.5")))
        self._default_lead_time.setValue(int(get_setting("default_lead_time_days", "14")))
        self._qoh_yellow_threshold.setValue(int(get_setting("qoh_yellow_threshold", "5")))

        # PO tab from DB
        self._po_default_ship_to.setPlainText(get_setting("po_default_ship_to", ""))
        self._po_default_bill_to.setPlainText(get_setting("po_default_bill_to", ""))
        self._load_saved_addresses()
        po_terms = get_setting("po_default_terms", "Net 30")
        idx = self._po_default_terms.findText(po_terms)
        if idx >= 0:
            self._po_default_terms.setCurrentIndex(idx)
        self._po_default_lead_time.setValue(int(get_setting("po_default_lead_time_days", "14")))
        self._po_default_shipping.setValue(float(get_setting("po_default_shipping", "0")))
        self._po_default_tax_rate.setValue(float(get_setting("po_default_tax_rate", "0")))
        self._po_number_prefix.setText(get_setting("po_number_prefix", "PO-"))
        self._po_default_notes.setPlainText(get_setting("po_default_notes", ""))
        self._po_sender_name.setText(get_setting("po_sender_name", ""))
        self._po_sender_email.setText(get_setting("po_sender_email", ""))
        send_method = get_setting("po_send_method", "manual")
        idx = self._po_send_method.findText(send_method)
        if idx >= 0:
            self._po_send_method.setCurrentIndex(idx)
        auto_merge = get_setting("po_auto_merge", "ask")
        idx = self._po_auto_merge.findText(auto_merge)
        if idx >= 0:
            self._po_auto_merge.setCurrentIndex(idx)
        self._po_restock_multiplier.setValue(float(get_setting("po_restock_multiplier", "1.0")))
        self._po_top_n_priority.setValue(int(get_setting("po_top_n_priority", "3")))
        self._po_focus_high_value.setValue(float(get_setting("po_focus_high_value", "5000")))
        self._po_cash_at_risk_warn.setValue(float(get_setting("po_cash_at_risk_warn", "10000")))

        # QBO tab from environment
        self._qbo_client_id.setText(os.environ.get("QBO_CLIENT_ID", ""))
        self._qbo_client_secret.setText(os.environ.get("QBO_CLIENT_SECRET", ""))
        self._qbo_realm_id.setText(os.environ.get("QBO_REALM_ID", ""))
        self._qbo_refresh_token.setPlainText(os.environ.get("QBO_REFRESH_TOKEN", ""))
        env = os.environ.get("QBO_ENVIRONMENT", "sandbox")
        self._qbo_sandbox.setChecked(env == "sandbox")
        write_enabled = os.environ.get("QBO_WRITE_ENABLED", "0") == "1"
        self._qbo_write_enabled.setChecked(write_enabled)
        # QBO.9 — seed mock / write checkboxes from DB if set, fall back
        # to env / auto-detection via qbo.config.
        try:
            from qbo.config import is_mock_mode, is_write_enabled, _creds_present
            creds_ok = _creds_present()
            self._qbo_mock_mode.setChecked(is_mock_mode())
            self._qbo_write_enabled.setChecked(is_write_enabled())
            if not creds_ok:
                # Hard-locked: qbo.config.is_mock_mode() always returns True
                # when credentials are missing, so the checkbox would just
                # re-tick itself on next reload. Make that visible.
                self._qbo_mock_mode.setEnabled(False)
                self._qbo_mock_mode.setToolTip(
                    "Mock Mode is forced ON because QBO credentials are "
                    "missing.\nFill in Client ID, Client Secret, and "
                    "Company ID below, click 'Save QBO Settings', then "
                    "restart the app to enable live mode."
                )
            else:
                self._qbo_mock_mode.setEnabled(True)
                self._qbo_mock_mode.setToolTip(
                    "When checked, all QBO writes are simulated with "
                    "deterministic stub responses. Turn off to allow live "
                    "QBO traffic (requires 'Enable QBO Write Operations')."
                )
        except Exception:
            self._qbo_mock_mode.setChecked(True)
        # D2 — load account-mapping fields from app_settings.
        try:
            from qbo.config import get_full_account_mapping, get_sync_authority
            mapping = get_full_account_mapping()
            self._qbo_acct_inventory.setCurrentText(mapping.get("inventory_asset", ""))
            self._qbo_acct_income.setCurrentText(mapping.get("sales_income", ""))
            self._qbo_acct_cogs.setCurrentText(mapping.get("cogs", ""))
            self._qbo_acct_core.setCurrentText(mapping.get("core_liability", ""))
            self._qbo_acct_ar.setCurrentText(mapping.get("ar", ""))
            self._qbo_acct_ap.setCurrentText(mapping.get("ap", ""))
            self._qbo_acct_tax.setCurrentText(mapping.get("tax_payable", ""))
            self._qbo_acct_freight_income.setCurrentText(mapping.get("freight_income", ""))
            self._qbo_acct_freight_expense.setCurrentText(mapping.get("freight_expense", ""))
            self._qbo_acct_discounts.setCurrentText(mapping.get("sales_discounts", ""))
            self._qbo_acct_returns.setCurrentText(mapping.get("sales_returns", ""))
            self._qbo_acct_warranty.setCurrentText(mapping.get("warranty_reserve", ""))
            # Sync authority
            auth = get_sync_authority()
            auth_map = {"local_master": 0, "qbo_master": 1, "hybrid": 2}
            self._qbo_sync_authority.setCurrentIndex(auth_map.get(auth, 0))
            # Tier 3 fields
            try:
                self._qbo_default_tax_code.setText(get_setting("qbo.default_tax_code", ""))
                self._qbo_default_currency.setText(get_setting("qbo.default_currency", ""))
                self._qbo_webhook_token.setText(get_setting("qbo.webhook_verifier_token", ""))
            except Exception:
                pass
        except Exception:
            pass
        self._update_qbo_status()
        try:
            self._refresh_qbo_connection_status()
        except Exception:
            pass

        # Email/SMTP tab from DB
        self._smtp_host.setText(get_setting("smtp_host", ""))
        self._smtp_port.setValue(int(get_setting("smtp_port", "587")))
        self._smtp_tls.setChecked(get_setting("smtp_tls", "1") == "1")
        self._smtp_user.setText(get_setting("smtp_user", ""))
        self._smtp_password.setText(get_setting("smtp_password", ""))
        self._smtp_from.setText(get_setting("smtp_from", ""))
        self._update_email_status()

        # Scraper tab from DB
        self._pai_username.setText(get_setting("pai_username", ""))
        self._pai_password.setText(get_setting("pai_password", ""))
        self._pai_base_url.setText(get_setting("pai_base_url", "https://store.paiindustries.com"))
        self._scrape_delay.setValue(float(get_setting("scrape_delay_sec", "2.0")))

        # Tax rates table
        self._load_tax_rates()

    def _save_company(self) -> None:
        pairs = {
            "company_name": self._company_name.text(),
            "company_address": self._company_address.text(),
            "company_city": self._company_city.text(),
            "company_state": self._company_state.text(),
            "company_zip": self._company_zip.text(),
            "company_phone": self._company_phone.text(),
            "company_email": self._company_email.text(),
            "company_website": self._company_website.text(),
            "company_logo_path": self._logo_path.text(),
        }
        for k, v in pairs.items():
            set_setting(k, v)
        QMessageBox.information(self, "Saved", "Company info saved.")

    def _save_invoice(self) -> None:
        set_setting("default_tax_rate", str(self._tax_rate.value()))
        set_setting("tax_home_state", self._tax_home_state.text().strip().upper())
        set_setting("tax_ship_from_zip", self._tax_ship_from_zip.text().strip())
        set_setting("tax_destination_sourced", "1" if self._tax_destination_sourced.isChecked() else "0")
        set_setting("tax_provider", self._tax_provider.currentText().strip().lower() or "manual")
        set_setting("taxjar_api_key", self._taxjar_api_key.text().strip())
        set_setting("taxjar_sandbox", "1" if self._taxjar_sandbox.isChecked() else "0")
        set_setting("payment_terms", self._payment_terms.currentText())
        set_setting("default_customer_terms", self._default_customer_terms.currentText())
        set_setting("invoice_prefix", self._invoice_prefix.text())
        set_setting("invoice_notes", self._invoice_notes.toPlainText())
        set_setting("show_core_charges", "1" if self._show_core_charges.isChecked() else "0")
        # Quote display settings
        set_setting("quote_show_sku", "1" if self._quote_show_sku.isChecked() else "0")
        set_setting("quote_show_warranty", "1" if self._quote_show_warranty.isChecked() else "0")
        set_setting("quote_show_core", "1" if self._quote_show_core.isChecked() else "0")
        set_setting("quote_show_notes", "1" if self._quote_show_notes.isChecked() else "0")
        set_setting("quote_show_freight", "1" if self._quote_show_freight.isChecked() else "0")
        set_setting("quote_show_company_address",
                    "1" if self._quote_show_company_address.isChecked() else "0")
        set_setting("quote_show_supplier",
                    "1" if self._quote_show_supplier.isChecked() else "0")
        set_setting("quote_logo_size_pct", str(self._quote_logo_size_pct.value()))
        set_setting("quote_default_terms", self._quote_default_terms.text().strip())
        set_setting("quote_default_expires_days", str(self._quote_default_expires_days.value()))
        set_setting("business_hq_zip", self._business_hq_zip.text().strip() or "82001")
        set_setting(
            "default_shipping_method",
            self._default_ship_method.currentText().strip() or "Will Call",
        )
        set_setting("default_shipping_fee", f"{self._default_ship_fee.value():.2f}")
        set_setting(
            "default_shipping_taxable",
            "1" if self._default_ship_taxable.isChecked() else "0",
        )
        QMessageBox.information(self, "Saved", "Invoice defaults saved.")

    def _save_inventory(self) -> None:
        set_setting("sku_prefix", self._sku_prefix.text())
        set_setting("low_stock_threshold", str(self._low_stock_threshold.value()))
        formula = "max_minus_onhand" if self._reorder_formula.currentIndex() == 0 else "fixed_qty"
        set_setting("reorder_formula", formula)
        set_setting("auto_barcode", "1" if self._auto_barcode.isChecked() else "0")
        set_setting("so_manual_po_mode", "1" if self._so_manual_po_mode.isChecked() else "0")
        set_setting("velocity_lookback_days", str(self._velocity_lookback.value()))
        set_setting("safety_stock_multiplier", str(self._safety_stock_mult.value()))
        set_setting("default_lead_time_days", str(self._default_lead_time.value()))
        set_setting("qoh_yellow_threshold", str(self._qoh_yellow_threshold.value()))
        QMessageBox.information(self, "Saved", "Inventory defaults saved.")

    def _save_po(self) -> None:
        set_setting("po_default_ship_to", self._po_default_ship_to.toPlainText().strip())
        set_setting("po_default_bill_to", self._po_default_bill_to.toPlainText().strip())
        set_setting("po_default_terms", self._po_default_terms.currentText())
        set_setting("po_default_lead_time_days", str(self._po_default_lead_time.value()))
        set_setting("po_default_shipping", str(self._po_default_shipping.value()))
        set_setting("po_default_tax_rate", str(self._po_default_tax_rate.value()))
        set_setting("po_number_prefix", self._po_number_prefix.text().strip())
        set_setting("po_default_notes", self._po_default_notes.toPlainText().strip())
        set_setting("po_sender_name", self._po_sender_name.text().strip())
        set_setting("po_sender_email", self._po_sender_email.text().strip())
        set_setting("po_send_method", self._po_send_method.currentText())
        set_setting("po_auto_merge", self._po_auto_merge.currentText())
        set_setting("po_restock_multiplier", str(self._po_restock_multiplier.value()))
        set_setting("po_top_n_priority", str(self._po_top_n_priority.value()))
        set_setting("po_focus_high_value", str(self._po_focus_high_value.value()))
        set_setting("po_cash_at_risk_warn", str(self._po_cash_at_risk_warn.value()))
        QMessageBox.information(self, "Saved", "Purchase Order defaults saved.")

    # ── Saved Address helpers ────────────────────────────────────
    def _saved_addresses_key(self) -> str:
        return "po_saved_ship_to_addresses"

    def _load_saved_addresses(self) -> None:
        raw = get_setting("po_saved_ship_to_addresses", "[]") or "[]"
        try:
            entries = json.loads(raw)
        except Exception:
            entries = []
        self._saved_addr_list.clear()
        for e in entries:
            name = e.get("name", "")
            addr_preview = (e.get("address", "").replace("\n", " ") or "")[:60]
            label = f"{name}  —  {addr_preview}" if name else addr_preview
            item = self._saved_addr_list.addItem(label)
        # Store raw entries for editing
        self._saved_addr_entries: list[dict] = list(entries)

    def _add_saved_address(self) -> None:
        from PySide6.QtWidgets import QFormLayout as FL
        dlg = QDialog(self)
        dlg.setWindowTitle("Add Saved Address")
        dlg.resize(400, 220)
        v = QVBoxLayout(dlg)
        form = FL()
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("e.g. Main Warehouse")
        form.addRow("Label:", name_edit)
        addr_edit = QTextEdit()
        addr_edit.setPlaceholderText("Full address (street, city, state, zip…)")
        addr_edit.setFixedHeight(100)
        form.addRow("Address:", addr_edit)
        v.addLayout(form)
        btns = QHBoxLayout()
        ok_btn = QPushButton("Add")
        ok_btn.clicked.connect(dlg.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dlg.reject)
        btns.addStretch()
        btns.addWidget(cancel_btn)
        btns.addWidget(ok_btn)
        v.addLayout(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name = name_edit.text().strip()
        address = addr_edit.toPlainText().strip()
        if not address:
            return
        if not hasattr(self, "_saved_addr_entries"):
            self._saved_addr_entries = []
        self._saved_addr_entries.append({"name": name, "address": address})
        set_setting("po_saved_ship_to_addresses", json.dumps(self._saved_addr_entries))
        self._load_saved_addresses()

    def _edit_saved_address(self) -> None:
        row = self._saved_addr_list.currentRow()
        if row < 0 or not hasattr(self, "_saved_addr_entries") or row >= len(self._saved_addr_entries):
            return
        entry = self._saved_addr_entries[row]
        from PySide6.QtWidgets import QFormLayout as FL
        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Saved Address")
        dlg.resize(400, 220)
        v = QVBoxLayout(dlg)
        form = FL()
        name_edit = QLineEdit(entry.get("name", ""))
        form.addRow("Label:", name_edit)
        addr_edit = QTextEdit()
        addr_edit.setPlainText(entry.get("address", ""))
        addr_edit.setFixedHeight(100)
        form.addRow("Address:", addr_edit)
        v.addLayout(form)
        btns = QHBoxLayout()
        ok_btn = QPushButton("Save")
        ok_btn.clicked.connect(dlg.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dlg.reject)
        btns.addStretch()
        btns.addWidget(cancel_btn)
        btns.addWidget(ok_btn)
        v.addLayout(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._saved_addr_entries[row] = {
            "name": name_edit.text().strip(),
            "address": addr_edit.toPlainText().strip(),
        }
        set_setting("po_saved_ship_to_addresses", json.dumps(self._saved_addr_entries))
        self._load_saved_addresses()

    def _remove_saved_address(self) -> None:
        row = self._saved_addr_list.currentRow()
        if row < 0 or not hasattr(self, "_saved_addr_entries") or row >= len(self._saved_addr_entries):
            return
        entry = self._saved_addr_entries[row]
        label = entry.get("name") or entry.get("address", "")[:40]
        reply = QMessageBox.question(
            self, "Remove Address",
            f"Remove '{label}' from saved addresses?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._saved_addr_entries.pop(row)
        set_setting("po_saved_ship_to_addresses", json.dumps(self._saved_addr_entries))
        self._load_saved_addresses()

    def _save_email(self) -> None:
        set_setting("smtp_host", self._smtp_host.text().strip())
        set_setting("smtp_port", str(self._smtp_port.value()))
        set_setting("smtp_tls", "1" if self._smtp_tls.isChecked() else "0")
        set_setting("smtp_user", self._smtp_user.text().strip())
        set_setting("smtp_password", self._smtp_password.text().strip())
        set_setting("smtp_from", self._smtp_from.text().strip())
        self._update_email_status()
        QMessageBox.information(self, "Saved", "Email / SMTP settings saved.")

    def _update_email_status(self) -> None:
        host = self._smtp_host.text().strip()
        user = self._smtp_user.text().strip()
        if host and user:
            self._email_status.setText("✅ SMTP configured")
            self._email_status.setStyleSheet("color: green; font-weight: bold;")
        else:
            self._email_status.setText("⚠️ SMTP not configured — fill in Host and Username")
            self._email_status.setStyleSheet("color: orange;")

    def _test_email(self) -> None:
        """Save current settings, then send a test email to the smtp_user address."""
        self._save_email()
        from jaks_inventory.utils.notification_service import email_configured, _send_email
        from jaks_inventory.utils.email_worker import EmailWorker

        if not email_configured():
            QMessageBox.warning(self, "Not Configured",
                                "Fill in SMTP Host and Username first, then save.")
            return

        to_addr = self._smtp_from.text().strip() or self._smtp_user.text().strip()
        self._email_status.setText("⏳ Sending test email…")
        self._email_worker = EmailWorker(
            lambda: _send_email(
                to_addr,
                "JAK'S Inventory — Test Email",
                '<div style="font-family:Arial;padding:20px;">'
                '<h2 style="color:#3d4a38;">Email is working!</h2>'
                '<p>Your SMTP settings are configured correctly.</p>'
                '<p style="color:gray;font-size:9pt;">— JAK\'S Inventory</p></div>',
            ),
            parent=self,
        )

        def _on_result(result: dict) -> None:
            if result.get("success"):
                self._email_status.setText("")
                QMessageBox.information(self, "Test Email Sent",
                                        f"Test email sent to {to_addr}.\nCheck your inbox.")
            else:
                self._email_status.setText("")
                QMessageBox.critical(self, "Test Failed", f"Error: {result.get('error')}")

        self._email_worker.finished_result.connect(_on_result)
        self._email_worker.start()

    def _save_scraper(self) -> None:
        set_setting("pai_username", self._pai_username.text().strip())
        set_setting("pai_password", self._pai_password.text().strip())
        base_url = self._pai_base_url.text().strip()
        if base_url:
            set_setting("pai_base_url", base_url)
        set_setting("scrape_delay_sec", str(self._scrape_delay.value()))
        QMessageBox.information(self, "Saved", "Scraper settings saved.")

    def _test_pai_login(self) -> None:
        """Open a headed Chrome window so the user can sign in to PAI manually.

        Saves current credentials first so the form fields are pre-filled, then
        delegates to ``pai_login_interactive`` which polls until login succeeds
        or the timeout elapses. The persistent session cookie carries forward
        to subsequent headless lookups.
        """
        # Persist what's typed so the headed browser uses it.
        username = self._pai_username.text().strip()
        password = self._pai_password.text().strip()
        if not username or not password:
            QMessageBox.warning(
                self,
                "Missing credentials",
                "Enter PAI Username and Password first, then click Test PAI Login.",
            )
            return
        set_setting("pai_username", username)
        set_setting("pai_password", password)
        base_url = self._pai_base_url.text().strip()
        if base_url:
            set_setting("pai_base_url", base_url)

        QMessageBox.information(
            self,
            "Test PAI Login",
            "A Chrome window will open at the PAI login page.\n\n"
            "• Credentials are pre-filled from this form.\n"
            "• Solve any CAPTCHA / 2FA prompts manually.\n"
            "• Close the dialog after you reach the dashboard.\n\n"
            "Click OK to continue.",
        )

        try:
            from ..scraper.pai_scraper import pai_login_interactive
        except Exception as exc:  # pragma: no cover — import guard
            QMessageBox.critical(
                self, "Test PAI Login", f"Could not import PAI scraper:\n{exc}"
            )
            return

        self._pai_login_worker = EmailWorker(
            lambda: {
                "ok": (result := pai_login_interactive(timeout_sec=180))[0],
                "message": result[1],
            },
            parent=self,
        )

        def _on_pai_login_result(result: dict) -> None:
            ok = bool(result.get("ok"))
            msg = str(result.get("message", "Unknown PAI login result."))
            if ok:
                QMessageBox.information(self, "Test PAI Login — Success", msg)
            else:
                QMessageBox.warning(self, "Test PAI Login — Failed", msg)

        self._pai_login_worker.finished_result.connect(_on_pai_login_result)
        self._pai_login_worker.start()

    # ------------------------------------------------------------------
    # Tax Rates
    # ------------------------------------------------------------------
    def _test_taxjar(self) -> None:
        """Validate the TaxJar API key by hitting the rates endpoint."""
        api_key = self._taxjar_api_key.text().strip()
        if not api_key:
            self._taxjar_status.setText(
                "<span style='color:#c0392b;'>Enter an API key first.</span>"
            )
            return
        sandbox = self._taxjar_sandbox.isChecked()
        self._taxjar_status.setText("Testing…")
        QApplication.processEvents()
        try:
            from jaks_inventory.utils.taxjar_client import test_connection
            ok, msg = test_connection(api_key, sandbox=sandbox)
        except Exception as e:
            ok, msg = False, f"{type(e).__name__}: {e}"
        color = "#27ae60" if ok else "#c0392b"
        self._taxjar_status.setText(f"<span style='color:{color};'>{msg}</span>")

    def _load_tax_rates(self) -> None:
        from db import list_tax_rates
        try:
            rows = list_tax_rates()
        except Exception:
            rows = []
        self._tax_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            it_state = QTableWidgetItem((r.get("state") or "").upper())
            it_state.setData(Qt.ItemDataRole.UserRole, r.get("id"))
            self._tax_table.setItem(i, 0, it_state)
            self._tax_table.setItem(i, 1, QTableWidgetItem(r.get("zip") or ""))
            rate_val = float(r.get("rate") or 0) * 100
            self._tax_table.setItem(i, 2, QTableWidgetItem(f"{rate_val:.3f}"))
            self._tax_table.setItem(i, 3, QTableWidgetItem(r.get("qbo_tax_code") or ""))
            self._tax_table.setItem(i, 4, QTableWidgetItem(r.get("description") or ""))

    def _add_tax_rate_row(self) -> None:
        row = self._tax_table.rowCount()
        self._tax_table.insertRow(row)
        defaults = ["", "", "0.000", "", ""]
        for c, val in enumerate(defaults):
            it = QTableWidgetItem(val)
            if c == 0:
                it.setData(Qt.ItemDataRole.UserRole, None)
            self._tax_table.setItem(row, c, it)
        self._tax_table.setCurrentCell(row, 0)
        self._tax_table.editItem(self._tax_table.item(row, 0))

    def _delete_tax_rate_row(self) -> None:
        from db import delete_tax_rate
        row = self._tax_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Select a Row", "Click a row to delete.")
            return
        it = self._tax_table.item(row, 0)
        rid = it.data(Qt.ItemDataRole.UserRole) if it else None
        confirm = QMessageBox.question(
            self, "Delete Tax Rate",
            "Delete this tax rate?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        if rid:
            try:
                delete_tax_rate(int(rid))
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Delete failed: {e}")
                return
        self._tax_table.removeRow(row)

    def _save_tax_rates(self) -> None:
        from db import upsert_tax_rate
        saved = 0
        errors: list[str] = []
        for row in range(self._tax_table.rowCount()):
            state_it = self._tax_table.item(row, 0)
            if not state_it or not state_it.text().strip():
                continue
            rid = state_it.data(Qt.ItemDataRole.UserRole)
            state = state_it.text().strip().upper()
            if len(state) != 2:
                errors.append(f"Row {row + 1}: state must be 2 characters")
                continue
            zip_it = self._tax_table.item(row, 1)
            zip_code = (zip_it.text().strip() if zip_it else "") or None
            rate_it = self._tax_table.item(row, 2)
            try:
                rate_pct = float((rate_it.text() if rate_it else "0") or "0")
            except ValueError:
                errors.append(f"Row {row + 1}: rate must be a number")
                continue
            code_it = self._tax_table.item(row, 3)
            qbo_code = (code_it.text().strip() if code_it else "") or None
            desc_it = self._tax_table.item(row, 4)
            desc = (desc_it.text().strip() if desc_it else "") or ""
            try:
                new_id = upsert_tax_rate(
                    int(rid) if rid else None,
                    state, zip_code, rate_pct / 100.0, desc, True,
                    qbo_tax_code=qbo_code,
                )
                state_it.setData(Qt.ItemDataRole.UserRole, new_id)
                saved += 1
            except Exception as e:
                errors.append(f"Row {row + 1}: {e}")
        if errors:
            QMessageBox.warning(
                self, "Some rows failed",
                f"Saved {saved} row(s).\n\nErrors:\n" + "\n".join(errors),
            )
        else:
            QMessageBox.information(self, "Saved", f"Saved {saved} tax rate(s).")
        self._load_tax_rates()

    # ------------------------------------------------------------------
    # Logo
    # ------------------------------------------------------------------
    def _browse_logo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Logo", "", "Images (*.png *.jpg *.jpeg *.bmp)"
        )
        if path:
            # Copy to assets/
            assets_dir = Path(__file__).parent.parent / "assets"
            assets_dir.mkdir(exist_ok=True)
            dest = assets_dir / Path(path).name
            shutil.copy2(path, dest)
            rel = f"assets/{dest.name}"
            self._logo_path.setText(rel)
            self._update_logo_preview()

    def _update_logo_preview(self) -> None:
        rel = self._logo_path.text()
        if not rel:
            return
        full = Path(__file__).parent.parent / rel
        if full.exists():
            pm = QPixmap(str(full))
            self._logo_preview.setPixmap(
                pm.scaled(120, 80, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )

    # ------------------------------------------------------------------
    # QBO
    # ------------------------------------------------------------------
    def _update_qbo_status(self) -> None:
        has_creds = all([
            os.environ.get("QBO_CLIENT_ID"),
            os.environ.get("QBO_CLIENT_SECRET"),
            os.environ.get("QBO_REALM_ID"),
        ])
        if has_creds:
            self._qbo_status.setText("Credentials configured")
            self._qbo_status.setStyleSheet("color: green;")
        else:
            self._qbo_status.setText("Credentials not configured (mock mode)")
            self._qbo_status.setStyleSheet("color: orange;")

    def _test_qbo_connection(self) -> None:
        if not QBO_AVAILABLE:
            QMessageBox.warning(self, "QBO Not Available",
                "QBO client module not found.\nInstall: pip install intuit-oauth quickbooks-python")
            return
        os.environ["QBO_CLIENT_ID"] = self._qbo_client_id.text()
        os.environ["QBO_CLIENT_SECRET"] = self._qbo_client_secret.text()
        os.environ["QBO_REALM_ID"] = self._qbo_realm_id.text()
        os.environ["QBO_REFRESH_TOKEN"] = self._qbo_refresh_token.toPlainText()
        os.environ["QBO_ENVIRONMENT"] = "sandbox" if self._qbo_sandbox.isChecked() else "production"
        try:
            client = QBOClient()
            if client.connect():
                if client.is_mock_mode:
                    QMessageBox.information(self, "Mock Mode",
                        "Connection test passed (mock mode - no real QBO credentials).")
                else:
                    items = client.fetch_all_items()
                    QMessageBox.information(self, "Connection Successful",
                        f"Connected to QuickBooks Online!\nFound {len(items)} items.")
            else:
                QMessageBox.critical(self, "Connection Failed", "Could not connect to QBO.")
        except Exception as e:
            QMessageBox.critical(self, "Connection Error", f"Error: {e}")

    def _save_qbo_settings(self) -> None:
        config_path = self._get_config_path()
        existing = {}
        if config_path.exists():
            with open(config_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        key, _, value = line.partition("=")
                        existing[key] = value
        existing["QBO_CLIENT_ID"] = self._qbo_client_id.text()
        existing["QBO_CLIENT_SECRET"] = self._qbo_client_secret.text()
        existing["QBO_REALM_ID"] = self._qbo_realm_id.text()
        existing["QBO_REFRESH_TOKEN"] = self._qbo_refresh_token.toPlainText().strip()
        existing["QBO_ENVIRONMENT"] = "sandbox" if self._qbo_sandbox.isChecked() else "production"
        existing["QBO_WRITE_ENABLED"] = "1" if self._qbo_write_enabled.isChecked() else "0"
        with open(config_path, "w") as f:
            for key, value in existing.items():
                f.write(f"{key}={value}\n")
        for key, value in existing.items():
            if key.startswith("QBO_"):
                os.environ[key] = value
        # QBO.9 — persist mock/write toggles to app_settings (DB) so they
        # survive restarts and are visible to every QBO module via the
        # central qbo.config helper.
        try:
            from qbo.config import set_mock_mode, set_write_enabled
            set_mock_mode(self._qbo_mock_mode.isChecked())
            set_write_enabled(self._qbo_write_enabled.isChecked())
        except Exception as e:
            log.warning("QBO toggle persistence failed: %s", e)
        self._update_qbo_status()
        QMessageBox.information(self, "Saved", f"QBO settings saved to {config_path}")

    def _save_qbo_account_mapping(self) -> None:
        """D2 — persist the QBO chart-of-accounts ids to app_settings."""
        try:
            from qbo.config import set_account_mapping, set_sync_authority
            auth_raw = self._qbo_sync_authority.currentText().lower().replace(" ", "_")
            if auth_raw not in ("local_master", "qbo_master", "hybrid"):
                auth_raw = "local_master"
            set_sync_authority(auth_raw)

            def _val(combo) -> str:
                # Prefer the selected item's data (always the bare QBO Id);
                # fall back to whatever the user typed (could be "Name (Id)"
                # or just the id).
                data = combo.currentData()
                if data:
                    return str(data).strip()
                raw = (combo.currentText() or "").strip()
                # Accept "Account Name (123)" → "123"
                if raw.endswith(")") and "(" in raw:
                    try:
                        return raw.rsplit("(", 1)[1].rstrip(")").strip()
                    except Exception:
                        return raw
                return raw

            set_account_mapping({
                "inventory_asset": _val(self._qbo_acct_inventory),
                "sales_income":    _val(self._qbo_acct_income),
                "cogs":            _val(self._qbo_acct_cogs),
                "core_liability":  _val(self._qbo_acct_core),
                "ar":              _val(self._qbo_acct_ar),
                "ap":              _val(self._qbo_acct_ap),
                "tax_payable":     _val(self._qbo_acct_tax),
                "freight_income":  _val(self._qbo_acct_freight_income),
                "freight_expense": _val(self._qbo_acct_freight_expense),
                "sales_discounts": _val(self._qbo_acct_discounts),
                "sales_returns":   _val(self._qbo_acct_returns),
                "warranty_reserve":_val(self._qbo_acct_warranty),
            })
            QMessageBox.information(
                self, "Saved",
                "QBO account mapping saved. New invoices/items will use "
                "these account ids on the next sync.",
            )
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not save mapping: {e}")

    def _save_qbo_tier3_settings(self) -> None:
        """Persist the Tier 3 tax / currency / webhook fields."""
        try:
            tax = (self._qbo_default_tax_code.text() or "").strip()
            ccy = (self._qbo_default_currency.text() or "").strip().upper()
            tok = (self._qbo_webhook_token.text() or "").strip()
            if ccy and (len(ccy) != 3 or not ccy.isalpha()):
                QMessageBox.warning(
                    self, "Invalid Currency",
                    "Currency must be a 3-letter ISO 4217 code (e.g. USD).",
                )
                return
            set_setting("qbo.default_tax_code", tax)
            set_setting("qbo.default_currency", ccy)
            set_setting("qbo.webhook_verifier_token", tok)
            QMessageBox.information(
                self, "Saved",
                "Tier 3 QBO settings saved. Tax / currency apply on the "
                "next invoice / bill push; webhook accepts inbound events "
                "as soon as the token is set.",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Error", f"Could not save: {exc}")

    def _refresh_qbo_accounts(self) -> None:
        """Fetch live chart of accounts from QBO and populate every combo.

        Each combo is filtered by AccountType (e.g. only Income accounts in
        the Sales Income picker). Preserves whatever id is currently
        selected so the user doesn't lose their typed value.
        """
        try:
            self._qbo_acct_refresh_status.setText("Loading…")
            self._qbo_acct_refresh_status.setStyleSheet("color: #555;")
            QApplication.processEvents()
        except Exception:
            pass
        try:
            from qbo.client import get_qbo_client
            from quickbooks.objects.account import Account
            client = get_qbo_client()
            if not client.connect():
                raise RuntimeError("QBO not connected — connect first in Connection panel.")
            accounts = Account.all(qb=client._client, max_results=1000)
        except Exception as exc:
            self._qbo_acct_refresh_status.setText(f"Failed: {exc}")
            self._qbo_acct_refresh_status.setStyleSheet("color: #b00;")
            return

        # Group by AccountType for quick filtering.
        by_type: dict[str, list[tuple[str, str]]] = {}
        for acc in accounts:
            t = str(getattr(acc, "AccountType", "") or "")
            name = str(getattr(acc, "Name", "") or "")
            acc_id = str(getattr(acc, "Id", "") or "")
            if not acc_id:
                continue
            by_type.setdefault(t, []).append((name, acc_id))

        widget_keys = [
            (self._qbo_acct_inventory,        "inventory_asset"),
            (self._qbo_acct_income,           "sales_income"),
            (self._qbo_acct_cogs,             "cogs"),
            (self._qbo_acct_core,             "core_liability"),
            (self._qbo_acct_ar,               "ar"),
            (self._qbo_acct_ap,               "ap"),
            (self._qbo_acct_tax,              "tax_payable"),
            (self._qbo_acct_freight_income,   "freight_income"),
            (self._qbo_acct_freight_expense,  "freight_expense"),
            (self._qbo_acct_discounts,        "sales_discounts"),
            (self._qbo_acct_returns,          "sales_returns"),
            (self._qbo_acct_warranty,         "warranty_reserve"),
        ]
        for combo, key in widget_keys:
            current = (combo.currentData() or combo.currentText() or "").strip()
            # Strip "Name (Id)" → Id if needed.
            if current.endswith(")") and "(" in current:
                try:
                    current = current.rsplit("(", 1)[1].rstrip(")").strip()
                except Exception:
                    pass
            allowed_types = self._qbo_acct_filters.get(key, ())
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("", "")
            matches: list[tuple[str, str]] = []
            for t in allowed_types:
                matches.extend(by_type.get(t, []))
            matches.sort(key=lambda p: p[0].lower())
            for name, acc_id in matches:
                combo.addItem(f"{name} ({acc_id})", acc_id)
            # Restore prior selection (by id) if it's still valid.
            if current:
                idx = combo.findData(current)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                else:
                    combo.setEditText(current)
            combo.blockSignals(False)

        self._qbo_acct_refresh_status.setText(
            f"Loaded {len(accounts)} accounts. Pick one per field and click Save."
        )
        self._qbo_acct_refresh_status.setStyleSheet("color: #2c7a2c;")

    # ------------------------------------------------------------------
    # QBO Connection / Sync (Integrations)
    # ------------------------------------------------------------------
    def _refresh_qbo_connection_status(self) -> None:
        """Repaint the Connection group from :mod:`qbo.oauth`."""
        try:
            from qbo.oauth import get_connection_status
            status = get_connection_status()
        except Exception as exc:
            self._qbo_conn_dot.setStyleSheet("color: gray; font-size: 16px;")
            self._qbo_conn_text.setText(f"Status error: {exc}")
            self._qbo_conn_realm.setText("—")
            self._qbo_conn_env.setText("—")
            self._qbo_conn_modes.setText("—")
            return

        if status.get("mock_mode"):
            self._qbo_conn_dot.setStyleSheet("color: #888; font-size: 16px;")
            self._qbo_conn_text.setText("Mock mode (no live API calls)")
        elif status.get("has_tokens"):
            self._qbo_conn_dot.setStyleSheet("color: #2c7a2c; font-size: 16px;")
            self._qbo_conn_text.setText("Connected")
        elif status.get("credentials_configured"):
            self._qbo_conn_dot.setStyleSheet("color: orange; font-size: 16px;")
            self._qbo_conn_text.setText("Not connected — click Connect…")
        else:
            self._qbo_conn_dot.setStyleSheet("color: #b00; font-size: 16px;")
            self._qbo_conn_text.setText(
                "Client ID/Secret missing (set in Advanced)"
            )

        self._qbo_conn_realm.setText(status.get("realm_id") or "—")
        self._qbo_conn_env.setText(status.get("environment") or "—")
        modes = []
        if status.get("mock_mode"):
            modes.append("Mock")
        if status.get("write_enabled"):
            modes.append("Write Enabled")
        else:
            modes.append("Read-Only")
        self._qbo_conn_modes.setText("  •  ".join(modes))

        # Enable Connect if Client ID + Secret are present (OAuth gives us the
        # Realm ID — we don't need it upfront).
        has_oauth_creds = bool(
            os.environ.get("QBO_CLIENT_ID", "").strip()
            and os.environ.get("QBO_CLIENT_SECRET", "").strip()
        )
        self._qbo_connect_btn.setEnabled(has_oauth_creds)
        if not has_oauth_creds:
            self._qbo_connect_btn.setToolTip(
                "Enter Client ID and Client Secret, then click "
                "'Save QBO Settings' to enable Connect."
            )
        else:
            self._qbo_connect_btn.setToolTip("")
        self._qbo_disconnect_btn.setEnabled(bool(status.get("has_tokens")))
        # Sync buttons always enabled — mock mode simulates them without live calls.
        for btn in (self._qbo_sync_customers_btn,
                    self._qbo_import_customers_btn,
                    self._qbo_sync_vendors_btn,
                    self._qbo_import_vendors_btn,
                    self._qbo_sync_invoices_btn,
                    self._qbo_sync_payments_btn,
                    self._qbo_sync_je_btn):
            btn.setEnabled(True)

    def _on_qbo_connect(self) -> None:
        """Kick off the OAuth flow in a background thread.

        The local HTTP server runs inside ``start_oauth_flow`` and blocks
        until the user finishes the redirect. We run that whole call on
        a worker thread to keep the UI responsive.
        """
        try:
            from qbo.oauth import start_oauth_flow  # noqa: F401
        except ImportError as exc:
            QMessageBox.critical(self, "QBO Unavailable", str(exc))
            return

        QMessageBox.information(
            self, "Connect to QuickBooks Online",
            "Your browser will open the Intuit sign-in page.\n\n"
            "Sign in, choose the company, and approve access. The window "
            "will say '✓ Connected' when done — you can close it then.",
        )

        worker = _QBOIntegrationsWorker(self, _kind="connect")
        worker.finished_with_result.connect(self._on_qbo_connect_done)
        self._qbo_connect_btn.setEnabled(False)
        self._qbo_connect_btn.setText("Connecting…")
        self._qbo_active_worker = worker  # keep reference
        worker.start()

    def _on_qbo_connect_done(self, result: dict) -> None:
        self._qbo_connect_btn.setText("Connect…")
        self._refresh_qbo_connection_status()
        if result.get("success"):
            QMessageBox.information(
                self, "Connected",
                f"Connected to QuickBooks Online.\n\n"
                f"Realm: {result.get('realm_id')}\n"
                f"Environment: {result.get('environment')}",
            )
            # Reflect new tokens into the Advanced fields so a manual
            # save still works.
            try:
                self._load_settings()
            except Exception:
                pass
        else:
            QMessageBox.critical(
                self, "Connection Failed",
                result.get("error") or "Unknown error.",
            )

    def _on_qbo_disconnect(self) -> None:
        reply = QMessageBox.question(
            self, "Disconnect QuickBooks?",
            "This will delete the stored refresh token. You will need to "
            "click Connect… again to reconnect.\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            from qbo.oauth import disconnect
            result = disconnect()
        except Exception as exc:
            QMessageBox.critical(self, "Disconnect Failed", str(exc))
            return
        self._refresh_qbo_connection_status()
        if result.get("had_tokens"):
            QMessageBox.information(self, "Disconnected",
                                    "QuickBooks Online tokens removed.")
        else:
            QMessageBox.information(self, "Already Disconnected",
                                    "No QBO tokens were stored.")

    # ── Sync handlers ───────────────────────────────────────────────
    def _run_qbo_sync(self, kind: str, label: str) -> None:
        worker = _QBOIntegrationsWorker(self, _kind=kind)
        worker.finished_with_result.connect(
            lambda result, k=label: self._on_qbo_sync_done(k, result)
        )
        # Disable all sync buttons during the run.
        for btn in (self._qbo_sync_customers_btn,
                    self._qbo_import_customers_btn,
                    self._qbo_sync_vendors_btn,
                    self._qbo_import_vendors_btn,
                    self._qbo_import_invoices_btn,
                    self._qbo_import_payments_btn,
                    self._qbo_import_credits_btn,
                    self._qbo_import_bills_btn,
                    self._qbo_import_pos_btn,
                    self._qbo_import_estimates_btn,
                    self._qbo_import_sales_receipts_btn,
                    self._qbo_import_refund_receipts_btn,
                    self._qbo_import_bill_payments_btn,
                    self._qbo_push_sales_receipts_btn,
                    self._qbo_push_refunds_btn,
                    self._qbo_push_bill_payments_btn,
                    self._qbo_import_tax_codes_btn,
                    self._qbo_import_classes_btn,
                    self._qbo_import_locations_btn,
                    self._qbo_sync_invoices_btn,
                    self._qbo_sync_payments_btn,
                    self._qbo_sync_je_btn):
            btn.setEnabled(False)
        self._qbo_active_worker = worker
        worker.start()

    def _on_qbo_sync_done(self, label: str, result: dict) -> None:
        for btn in (self._qbo_sync_customers_btn,
                    self._qbo_import_customers_btn,
                    self._qbo_sync_vendors_btn,
                    self._qbo_import_vendors_btn,
                    self._qbo_import_invoices_btn,
                    self._qbo_import_payments_btn,
                    self._qbo_import_credits_btn,
                    self._qbo_import_bills_btn,
                    self._qbo_import_pos_btn,
                    self._qbo_import_estimates_btn,
                    self._qbo_import_sales_receipts_btn,
                    self._qbo_import_refund_receipts_btn,
                    self._qbo_import_bill_payments_btn,
                    self._qbo_push_sales_receipts_btn,
                    self._qbo_push_refunds_btn,
                    self._qbo_push_bill_payments_btn,
                    self._qbo_import_tax_codes_btn,
                    self._qbo_import_classes_btn,
                    self._qbo_import_locations_btn,
                    self._qbo_sync_invoices_btn,
                    self._qbo_sync_payments_btn,
                    self._qbo_sync_je_btn):
            btn.setEnabled(True)

        if "error" in result and result.get("error"):
            QMessageBox.critical(self, f"{label} Failed", str(result["error"]))
            return

        # Customer sync / import uses {total, created, linked, updated, skipped, errors}.
        if "total" in result:
            is_import = "import" in label.lower()
            created_label = "Imported (new):" if is_import else "Created in QBO:"
            linked_label  = "Linked to QBO: " if is_import else "Linked existing:"
            errors_raw = result.get("errors") or []
            errors_text = (
                "\n  \u2022 " + "\n  \u2022 ".join(str(e) for e in errors_raw)
                if errors_raw else "None"
            )
            updated_line = ""
            if "updated" in result:
                updated_line = f"Updated:          {result.get('updated', 0)}\n"
            linked_line = ""
            if "linked" in result:
                linked_line = f"{linked_label}   {result.get('linked', 0)}\n"
            extra_lines = ""
            if "invoices_reconciled" in result:
                extra_lines += (
                    f"Invoices reconciled: "
                    f"{result.get('invoices_reconciled', 0)}\n"
                )
            if "matched" in result:
                extra_lines += f"Matched POs:      {result.get('matched', 0)}\n"
            if "core_returns_linked" in result:
                extra_lines += (
                    f"Core returns:     "
                    f"{result.get('core_returns_linked', 0)}\n"
                )
            if "lines_synced" in result:
                extra_lines += (
                    f"Lines synced:     "
                    f"{result.get('lines_synced', 0)}\n"
                )
            if "po_reconciled" in result:
                extra_lines += (
                    f"POs reconciled:   "
                    f"{result.get('po_reconciled', 0)}\n"
                )
            QMessageBox.information(
                self, f"{label} Complete",
                f"QBO total:        {result.get('total', 0)}\n"
                f"{created_label}  {result.get('created', 0)}\n"
                f"{linked_line}"
                f"{updated_line}"
                f"{extra_lines}"
                f"Skipped:          {result.get('skipped', 0)}\n"
                f"Errors:           {len(errors_raw)}{errors_text}",
            )
            return

        QMessageBox.information(
            self, f"{label} Complete",
            f"OK:      {result.get('ok', 0)}\n"
            f"Skipped: {result.get('skipped', 0)}\n"
            f"Failed:  {result.get('failed', 0)}",
        )

    # ── UI helpers (sync section styling) ────────────────────────────
    _QBO_BTN_STYLES = {
        "push": (
            "QPushButton { background-color: #e8f5e9; color: #1b5e20; "
            "border: 1px solid #4caf50; border-radius: 3px; "
            "padding: 6px 12px; font-weight: 600; }"
            "QPushButton:hover { background-color: #c8e6c9; }"
            "QPushButton:disabled { background-color: #f1f8e9; "
            "color: #9e9e9e; border-color: #c5e1a5; }"
        ),
        "import": (
            "QPushButton { background-color: #e3f2fd; color: #0d47a1; "
            "border: 1px solid #2196f3; border-radius: 3px; "
            "padding: 6px 12px; font-weight: 600; }"
            "QPushButton:hover { background-color: #bbdefb; }"
            "QPushButton:disabled { background-color: #f3f8fd; "
            "color: #9e9e9e; border-color: #bbdefb; }"
        ),
        "danger": (
            "QPushButton { background-color: #fff3e0; color: #e65100; "
            "border: 2px solid #ff9800; border-radius: 3px; "
            "padding: 8px 16px; font-weight: 700; }"
            "QPushButton:hover { background-color: #ffe0b2; }"
            "QPushButton:disabled { background-color: #fff8e1; "
            "color: #9e9e9e; border-color: #ffe0b2; }"
        ),
    }

    def _style_qbo_btn(self, btn, kind: str) -> None:
        """Apply direction-coded styling to a QBO sync button.

        kind: 'push' (green, app→QBO), 'import' (blue, QBO→app),
        or 'danger' (orange, overwrite operation).
        """
        css = self._QBO_BTN_STYLES.get(kind)
        if css:
            btn.setStyleSheet(css)

    def _make_collapsible_section(
        self, title: str, subtitle: str = "", *, start_open: bool = False,
    ):
        """Build (header_button, body_widget) for a collapsible section.

        Clicking the header toggles body_widget visibility. Caller is
        responsible for setting a layout on body_widget and adding both
        widgets to its parent layout.
        """
        header = QPushButton(("▾  " if start_open else "▸  ") + title)
        header.setCheckable(True)
        header.setChecked(start_open)
        header.setStyleSheet(
            "QPushButton { text-align: left; padding: 8px 12px; "
            "font-weight: 700; font-size: 13px; "
            "background-color: #f5f5f5; border: 1px solid #ddd; "
            "border-radius: 3px; }"
            "QPushButton:hover { background-color: #eeeeee; }"
            "QPushButton:checked { background-color: #e8eaf6; "
            "border-color: #9fa8da; }"
        )
        body = QWidget()
        body.setVisible(start_open)
        if subtitle:
            header.setToolTip(subtitle)

        def _toggle(checked: bool) -> None:
            body.setVisible(checked)
            header.setText(
                ("▾  " if checked else "▸  ") + title
            )

        header.toggled.connect(_toggle)
        return header, body

    def _on_sync_customers(self) -> None:
        self._run_qbo_sync("customers", "Sync Customers")

    def _on_import_customers(self) -> None:
        self._run_qbo_sync("import_customers", "Import Customers from QBO")

    def _on_sync_vendors(self) -> None:
        self._run_qbo_sync("vendors", "Sync Vendors")

    def _on_import_vendors(self) -> None:
        self._run_qbo_sync("import_vendors", "Import Vendors from QBO")

    def _on_import_invoices(self) -> None:
        self._run_qbo_sync("import_invoices", "Import Invoices from QBO")

    def _on_import_payments(self) -> None:
        self._run_qbo_sync("import_payments", "Import Payments from QBO")

    def _on_import_credits(self) -> None:
        self._run_qbo_sync("import_credits", "Import Credit Memos from QBO")

    def _on_import_bills(self) -> None:
        self._run_qbo_sync("import_bills", "Import Bills from QBO")

    def _on_import_pos(self) -> None:
        self._run_qbo_sync("import_pos", "Import Purchase Orders from QBO")

    def _on_import_estimates(self) -> None:
        self._run_qbo_sync("import_estimates", "Import Estimates from QBO")

    def _on_import_sales_receipts(self) -> None:
        self._run_qbo_sync(
            "import_sales_receipts", "Import Sales Receipts from QBO"
        )

    def _on_import_refund_receipts(self) -> None:
        self._run_qbo_sync(
            "import_refund_receipts", "Import Refund Receipts from QBO"
        )

    def _on_import_bill_payments(self) -> None:
        self._run_qbo_sync(
            "import_bill_payments", "Import Bill Payments from QBO"
        )

    def _on_push_sales_receipts(self) -> None:
        self._run_qbo_sync(
            "push_sales_receipts", "Push Sales Receipts to QBO"
        )

    def _on_push_refunds(self) -> None:
        self._run_qbo_sync("push_refunds", "Push Refunds to QBO")

    def _on_push_bill_payments(self) -> None:
        self._run_qbo_sync(
            "push_bill_payments", "Push Bill Payments to QBO"
        )

    def _on_import_tax_codes(self) -> None:
        self._run_qbo_sync("import_tax_codes", "Import Tax Codes from QBO")

    def _on_import_classes(self) -> None:
        self._run_qbo_sync("import_classes", "Import Classes from QBO")

    def _on_import_locations(self) -> None:
        self._run_qbo_sync("import_locations", "Import Locations from QBO")

    def _on_sync_invoices(self) -> None:
        self._run_qbo_sync("invoices", "Sync Invoices")

    def _on_sync_payments(self) -> None:
        self._run_qbo_sync("payments", "Sync Payments")

    def _on_sync_journal_entries(self) -> None:
        self._run_qbo_sync("journal_entries", "Sync Journal Entries")

    # ------------------------------------------------------------------
    # System
    # ------------------------------------------------------------------
    def _open_log_folder(self) -> None:
        from pathlib import Path
        log_dir = Path(__file__).resolve().parent.parent.parent / "output" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir)))

    def _backup_database(self) -> None:
        from db.database import get_database_url
        db_url = get_database_url()
        if "sqlite" not in db_url:
            QMessageBox.warning(self, "Not Supported", "Backup only available for SQLite databases.")
            return
        db_path = db_url.replace("sqlite:///", "")
        src = Path(db_path)
        if not src.exists():
            # Try relative to workspace
            src = Path(__file__).parent.parent / db_path
        if not src.exists():
            QMessageBox.warning(self, "Not Found", f"Database file not found: {db_path}")
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = src.parent / f"inventory_backup_{ts}.db"
        shutil.copy2(src, dest)
        QMessageBox.information(self, "Backup Complete", f"Database backed up to:\n{dest}")

    def _reset_all_data(self) -> None:
        from db import reset_all_data

        reply = QMessageBox.warning(
            self, "Confirm Reset",
            "This will DELETE ALL DATA:\n\n"
            "- All products, customers, vendors\n"
            "- All invoices, POs, sales orders\n"
            "- All serials, cores, transfers\n\n"
            "Settings and QBO credentials are preserved.\n\n"
            "Are you sure?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        reply2 = QMessageBox.critical(
            self, "FINAL WARNING",
            "This cannot be undone!\nClick Yes to proceed.",
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

    def _reset_to_demo_data(self) -> None:
        """Wipe all data then re-seed with the demo set. Two-step confirm."""
        from db import reset_all_data
        from db.demo_seed import seed_demo

        reply = QMessageBox.warning(
            self, "Reset to Demo Data?",
            "This will DELETE ALL CURRENT DATA and replace it with a fresh\n"
            "demo data set (products, part numbers, cross-references).\n\n"
            "Settings and QBO credentials are preserved.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        text, ok = QInputDialog.getText(
            self, "Confirm Reset",
            'Type "RESET" (in capitals) to confirm:',
        )
        if not ok or text.strip() != "RESET":
            QMessageBox.information(self, "Cancelled", "Reset cancelled — confirmation text did not match.")
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            wipe_results = reset_all_data()
            seed_results = seed_demo(count=25)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Reset Error", f"Reset to demo data failed:\n{e}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        wiped = sum(v for v in wipe_results.values() if v > 0)
        QMessageBox.information(
            self, "Demo Data Loaded",
            f"Reset complete.\n\n"
            f"Wiped: {wiped} records\n"
            f"Seeded: {seed_results.get('created_products', 0)} products, "
            f"{seed_results.get('created_part_numbers', 0)} part numbers, "
            f"{seed_results.get('created_xref_links', 0)} xref links.\n\n"
            "Restart the app (or refresh each screen) to see the new data."
        )

    def _load_sandbox_data(self) -> None:
        """Seed sandbox test data (additive / idempotent)."""
        reply = QMessageBox.question(
            self, "Load Sandbox Data?",
            "This will add:\n"
            "  • 4 vendors (PAI, SAMPA, Stadium, NEED)\n"
            "  • 4 test customers\n"
            "  • 15 diesel-parts products\n\n"
            "Existing records with the same SKU/name are skipped.\n"
            "It is safe to run multiple times.\n\nProceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            import importlib.util
            import sys as _sys
            # Load the script without requiring it to be in the package
            spec = importlib.util.spec_from_file_location(
                "seed_sandbox",
                str(Path(__file__).resolve().parent.parent.parent / "scripts" / "seed_sandbox.py"),
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            results = mod.seed_sandbox(verbose=False)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Seed Error", f"Sandbox seed failed:\n{e}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        QMessageBox.information(
            self, "Sandbox Data Loaded",
            f"Sandbox seed complete!\n\n"
            f"Vendors:   {results.get('vendors_created', 0)} created, "
            f"{results.get('vendors_skipped', 0)} skipped\n"
            f"Customers: {results.get('customers_created', 0)} created, "
            f"{results.get('customers_skipped', 0)} skipped\n"
            f"Products:  {results.get('products_created', 0)} created, "
            f"{results.get('products_skipped', 0)} skipped\n"
            f"OEM xrefs: {results.get('xrefs_added', 0)} added\n\n"
            "Navigate to Products, Vendors, and Customers to see the new data."
        )

    # ------------------------------------------------------------------
    # QBO Product Sync (Product & Inventory Sync group)
    # ------------------------------------------------------------------

    def _run_product_sync(self, kind: str, label: str) -> None:
        """Launch a background worker for product-sync operations."""
        worker = _QBOIntegrationsWorker(self, _kind=kind)
        worker.finished_with_result.connect(
            lambda result, k=label: self._on_product_sync_done(k, result)
        )
        for btn in (self._qbo_sync_products_btn,
                    self._qbo_import_products_btn,
                    self._qbo_push_qty_btn):
            btn.setEnabled(False)
        self._qbo_active_product_worker = worker
        worker.start()

    def _on_product_sync_done(self, label: str, result: dict) -> None:
        for btn in (self._qbo_sync_products_btn,
                    self._qbo_import_products_btn,
                    self._qbo_push_qty_btn):
            btn.setEnabled(True)
        self._populate_qbo_status_table()

        if result.get("error"):
            QMessageBox.critical(self, f"{label} Failed", str(result["error"]))
            return

        # Import shape: {total, matched, unmatched, imported, errors}
        if "matched" in result:
            errors_raw = result.get("errors") or []
            errors_text = (
                "\n  \u2022 " + "\n  \u2022 ".join(str(e) for e in errors_raw[:10])
                if errors_raw else "None"
            )
            QMessageBox.information(
                self, f"{label} Complete",
                f"QBO items fetched:    {result.get('total', 0)}\n"
                f"Matched local SKU:    {result.get('matched', 0)}\n"
                f"No local match:       {result.get('unmatched', 0)}\n"
                f"Imported to local DB: {result.get('imported', 0)}\n"
                f"Errors:               {len(errors_raw)}{errors_text}",
            )
            return

        # Push shape: {ok, created, updated, skipped, failed}
        QMessageBox.information(
            self, f"{label} Complete",
            f"Created: {result.get('created', 0)}\n"
            f"Updated: {result.get('updated', 0)}\n"
            f"Skipped: {result.get('skipped', 0)}\n"
            f"Failed:  {result.get('failed', 0)}",
        )

    def _on_sync_products(self) -> None:
        self._run_product_sync("sync_products", "Sync Products \u2192QBO")

    def _on_import_products(self) -> None:
        self._run_product_sync("import_products_qbo", "Import Products \u2190QBO")

    def _on_push_inventory_qty(self) -> None:
        # Dry-run preview first — operator must confirm before any
        # write hits QuickBooks. Cancel from the dialog is a no-op.
        dlg = _QBOInventoryQtyPreviewDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._run_product_sync("push_inventory_qty", "Push Inventory Qty \u2192QBO")

    # ------------------------------------------------------------------
    # QBO status table
    # ------------------------------------------------------------------

    def _populate_qbo_status_table(self) -> None:
        """Fill the sync status QTableWidget from qbo_sync_log."""
        # Keep the env banner in sync — cheap, runs on every refresh.
        try:
            self._refresh_qbo_env_banner()
        except Exception:
            pass
        try:
            from db.database import get_db
            from PySide6.QtWidgets import QTableWidgetItem as _QTI
            with get_db() as conn:
                rows = conn.execute(
                    """SELECT COALESCE(entity_type, 'unknown') AS entity_type,
                              MAX(updated_at) AS last_sync,
                              SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS ok,
                              SUM(CASE WHEN status='failed'  THEN 1 ELSE 0 END) AS fail
                       FROM qbo_sync_log
                       GROUP BY COALESCE(entity_type, 'unknown')
                       ORDER BY entity_type"""
                ).fetchall()
        except Exception:
            rows = []

        try:
            from qbo.sync_queue import pending_count_by_entity
            pend_map = pending_count_by_entity()
        except Exception:
            pend_map = {}

        # Hide internal/debug entity types from the user-facing table.
        _HIDDEN = {"mystery_thing", "test_entity", "debug"}

        self._qbo_status_table.setRowCount(0)
        # Build a unified set of entities (sync log + pending queue).
        seen: dict[str, dict] = {}
        for row in rows:
            entity, last_sync, ok, fail = (
                (row[0], row[1], row[2], row[3])
                if isinstance(row, tuple)
                else (row["entity_type"], row["last_sync"], row["ok"], row["fail"])
            )
            ent_norm = (entity or "").strip().lower()
            if ent_norm in _HIDDEN:
                continue
            seen[ent_norm or "unknown"] = {
                "entity": entity, "last_sync": last_sync,
                "ok": ok or 0, "fail": fail or 0,
                "pending": pend_map.pop(ent_norm, 0),
            }
        # Any pending-queue-only entities (not yet in sync log).
        for ent_norm, cnt in pend_map.items():
            if ent_norm in _HIDDEN:
                continue
            seen[ent_norm or "unknown"] = {
                "entity": ent_norm, "last_sync": None,
                "ok": 0, "fail": 0, "pending": cnt,
            }

        if not seen:
            self._qbo_status_table.setRowCount(1)
            self._qbo_status_table.setItem(0, 0, _QTI("No sync history yet"))
            for col in range(1, 5):
                self._qbo_status_table.setItem(0, col, _QTI("—"))
            return

        for i, (_norm, info) in enumerate(sorted(seen.items())):
            self._qbo_status_table.insertRow(i)
            self._qbo_status_table.setItem(
                i, 0, _QTI(_pretty_entity(info["entity"])))
            self._qbo_status_table.setItem(i, 1, _QTI(str(info["pending"])))
            self._qbo_status_table.setItem(
                i, 2, _QTI(str(info["last_sync"] or "—")))
            self._qbo_status_table.setItem(i, 3, _QTI(str(info["ok"])))
            self._qbo_status_table.setItem(i, 4, _QTI(str(info["fail"])))

    def _open_qbo_failures_dialog(self) -> None:
        """Open the QBO failed-sync replay dialog.

        Lists every ``qbo_sync_log`` row with ``status='failed'`` that
        hasn't been marked resolved yet. The user can review the error,
        mark items resolved, or refresh the list.
        """
        dlg = _QBOFailuresDialog(self)
        dlg.exec()
        # Refresh the status table after dialog closes — resolved rows
        # drop out of the failed count.
        self._populate_qbo_status_table()

    def _open_qbo_pending_dialog(self) -> None:
        """Open the QBO pending-sync queue dialog."""
        dlg = _QBOPendingQueueDialog(self)
        dlg.exec()
        self._populate_qbo_status_table()

    def _open_qbo_sync_log_dialog(self) -> None:
        """Open the append-only Sync Event Log dialog."""
        dlg = _QBOSyncLogDialog(self)
        dlg.exec()
        self._populate_qbo_status_table()

    def _refresh_qbo_env_banner(self) -> None:
        """Update the always-visible environment banner.

        Three states:
        * mock          — gray "Mock Mode" banner
        * sandbox       — orange "Sandbox" banner (no live data modified)
        * production    — red "Live" banner with explicit warning
        """
        try:
            from qbo.config import describe as _qbo_describe
            info = _qbo_describe() or {}
        except Exception:
            info = {}
        mock = bool(info.get("mock"))
        # Pull environment hint from env var (sandbox/production).
        env = (os.environ.get("QBO_ENVIRONMENT") or "sandbox").strip().lower()

        if mock:
            self._qbo_env_banner.setText(
                "🧪  <b>MOCK MODE ACTIVE</b> — no QuickBooks API calls are "
                "being made. Enable credentials to switch to live mode."
            )
            self._qbo_env_banner.setStyleSheet(
                "QLabel { background-color: #ECEFF1; color: #455A64; "
                "padding: 8px; border: 1px solid #B0BEC5; "
                "border-radius: 4px; font-size: 12px; }"
            )
        elif env == "production":
            self._qbo_env_banner.setText(
                "🔴  <b>LIVE QUICKBOOKS</b> — every action touches your "
                "production company file. Proceed with care."
            )
            self._qbo_env_banner.setStyleSheet(
                "QLabel { background-color: #FFEBEE; color: #B71C1C; "
                "padding: 8px; border: 2px solid #E53935; "
                "border-radius: 4px; font-size: 12px; font-weight: 600; }"
            )
        else:
            self._qbo_env_banner.setText(
                "🟠  <b>SANDBOX MODE ACTIVE</b> — no live QBO data is "
                "being modified. Disable “Use Sandbox Environment” to "
                "switch to production."
            )
            self._qbo_env_banner.setStyleSheet(
                "QLabel { background-color: #FFF3E0; color: #E65100; "
                "padding: 8px; border: 1px solid #FFB74D; "
                "border-radius: 4px; font-size: 12px; }"
            )


# ---------------------------------------------------------------------------
# Module-level helpers used by the failure / pending dialogs
# ---------------------------------------------------------------------------

_PRETTY_ENTITIES: dict[str, str] = {
    "invoice":         "Invoice",
    "payment":         "Payment",
    "credit_memo":     "Credit Memo",
    "bill":            "Bill",
    "purchase_order":  "Purchase Order",
    "po":              "Purchase Order",
    "estimate":        "Estimate",
    "estimate_update": "Estimate",
    "quote":           "Estimate",
    "customer":        "Customer",
    "vendor":          "Vendor",
    "product":         "Product",
    "item":            "Product",
    "sales_receipt":   "Sales Receipt",
    "refund_receipt":  "Refund Receipt",
    "bill_payment":    "Bill Payment",
    "journal_entry":   "Journal Entry",
    "tax_code":        "Tax Code",
    "class":           "Class",
    "location":        "Location",
}


def _pretty_entity(raw: str | None) -> str:
    """Return a human-friendly entity label.

    Internal/debug names (e.g. ``mystery_thing``) fall back to ``Other``
    so unit-test artifacts never surface in the UI.
    """
    if not raw:
        return "—"
    key = str(raw).strip().lower()
    if key in _PRETTY_ENTITIES:
        return _PRETTY_ENTITIES[key]
    # Anything not in the allow-list is an internal/debug entity.
    return "Other"


def _fmt_when(ts: str | None) -> str:
    """Render an ISO timestamp as a short "Xm ago" / "Xh ago" string."""
    if not ts or ts == "—":
        return "—"
    from datetime import datetime
    try:
        # Tolerate "YYYY-MM-DD HH:MM:SS" and ISO "YYYY-MM-DDTHH:MM:SS".
        s = str(ts).replace("T", " ").split(".")[0]
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)
    delta = datetime.utcnow() - dt
    secs = int(delta.total_seconds())
    if secs < 0:
        return dt.strftime("%Y-%m-%d %H:%M")
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    if secs < 7 * 86400:
        return f"{secs // 86400}d ago"
    return dt.strftime("%Y-%m-%d %H:%M")


def _cat_color(category: str):
    """Return a soft background QColor for an error category pill."""
    from PySide6.QtGui import QColor
    palette = {
        "Duplicate":          "#FFE0B2",   # orange
        "Missing Reference":  "#FFCCBC",   # deep orange
        "Invalid Tax Code":   "#F8BBD0",   # pink
        "Invalid Account":    "#F8BBD0",
        "Validation Error":   "#FFF59D",   # yellow
        "Permission Denied":  "#EF9A9A",   # red
        "Authentication":     "#EF9A9A",
        "Rate Limited":       "#C5CAE9",   # indigo
        "Network Failure":    "#B3E5FC",   # light blue
        "QBO Unavailable":    "#B3E5FC",
        "Not Found":          "#D7CCC8",   # brown
        "Business Rule":      "#DCEDC8",   # light green
        "Other":              "#E0E0E0",   # grey
    }
    return QColor(palette.get(category, "#E0E0E0"))


class _QBOFailuresDialog(QDialog):
    """Modal dialog listing every unresolved QBO sync failure.

    Reads ``qbo_sync_log`` for rows where ``status='failed'`` and
    ``resolved_at IS NULL``. Each row exposes a Mark Resolved action
    that stamps ``resolved_at = now()`` so it stops appearing here.
    """

    def __init__(self, parent=None, *, entity_filter: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(
            "QBO Sync Failures"
            + (f" — {entity_filter}" if entity_filter else "")
        )
        self.resize(1000, 540)
        self._entity_filter = entity_filter or None

        layout = QVBoxLayout(self)

        hint = QLabel(
            "Unresolved QBO push failures. Tick rows then use ‘Retry "
            "Selected’, or ‘Retry All’ after an outage. Click ‘Open Record’ "
            "to jump to the related invoice/PO/etc., or ‘Open in QBO’ to "
            "inspect a partially-created document on Intuit’s side."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #555; font-style: italic;")
        layout.addWidget(hint)

        # Filter row: category dropdown + entity filter readback
        from PySide6.QtWidgets import QComboBox as _QCB
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Category:"))
        self._cat_combo = _QCB()
        self._cat_combo.addItem("All", None)
        self._cat_combo.currentIndexChanged.connect(self._apply_category_filter)
        filter_row.addWidget(self._cat_combo)
        filter_row.addStretch()
        self._summary_lbl = QLabel("")
        self._summary_lbl.setStyleSheet("color: #555;")
        filter_row.addWidget(self._summary_lbl)
        layout.addLayout(filter_row)

        # Columns: ☐ | First Failed | Last Attempt | Entity | Action |
        #          Local Ref | Category | Friendly Error | Retries |
        #          Details | Open Record | QBO
        self._COL_CHECK   = 0
        self._COL_FIRST   = 1
        self._COL_LAST    = 2
        self._COL_ENTITY  = 3
        self._COL_ACTION  = 4
        self._COL_LOCAL   = 5
        self._COL_CAT     = 6
        self._COL_ERROR   = 7
        self._COL_RETRIES = 8
        self._COL_DETAILS = 9
        self._COL_OPEN    = 10
        self._COL_QBO     = 11
        self._table = QTableWidget(0, 12)
        self._table.setHorizontalHeaderLabels([
            "", "First Failed", "Last Attempt", "Entity", "Action",
            "Local Ref", "Category", "Error", "Retries",
            "Details", "Open Record", "QBO",
        ])
        hh = self._table.horizontalHeader()
        for c in (0, 1, 2, 3, 4, 5, 6, 8, 9, 10, 11):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(self._COL_ERROR, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._table.verticalHeader().setDefaultSectionSize(28)
        layout.addWidget(self._table, 1)

        btn_row = QHBoxLayout()
        self._check_all_btn = QPushButton("☑ Check All")
        self._check_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        self._uncheck_all_btn = QPushButton("☐ Uncheck")
        self._uncheck_all_btn.clicked.connect(lambda: self._set_all_checked(False))
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self._reload)
        self._retry_btn = QPushButton("↻ Retry Selected")
        self._retry_btn.setStyleSheet(
            "QPushButton { background-color: #E3F2FD; font-weight: 600; padding: 4px 12px; }"
        )
        self._retry_btn.clicked.connect(self._retry_selected)
        self._retry_all_btn = QPushButton("↻↻ Retry All")
        self._retry_all_btn.setStyleSheet(
            "QPushButton { background-color: #C8E6C9; font-weight: 600; padding: 4px 12px; }"
        )
        self._retry_all_btn.setToolTip(
            "Retry every visible failure. Useful after an outage, token "
            "refresh, or QBO downtime is resolved."
        )
        self._retry_all_btn.clicked.connect(self._retry_all)
        self._ignore_btn = QPushButton("⊘ Mark Ignored")
        self._ignore_btn.setStyleSheet(
            "QPushButton { background-color: #ECEFF1; }"
        )
        self._ignore_btn.setToolTip(
            "Hide the checked failure(s) without retrying. Ignored rows "
            "stay in the DB for audit but don't appear in Retry All."
        )
        self._ignore_btn.clicked.connect(self._ignore_selected)
        self._resolve_all_btn = QPushButton("Mark All Resolved")
        self._resolve_all_btn.setStyleSheet(
            "QPushButton { background-color: #f0e7c8; }"
        )
        self._resolve_all_btn.clicked.connect(self._resolve_all)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._check_all_btn)
        btn_row.addWidget(self._uncheck_all_btn)
        btn_row.addSpacing(12)
        btn_row.addWidget(self._refresh_btn)
        btn_row.addWidget(self._retry_btn)
        btn_row.addWidget(self._retry_all_btn)
        btn_row.addWidget(self._ignore_btn)
        btn_row.addWidget(self._resolve_all_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

        # All loaded rows kept here so the category filter can hide/show
        # without re-querying the DB.
        self._all_rows: list[dict] = []
        self._reload()

    def _reload(self) -> None:
        try:
            from db.database import get_db
            with get_db() as conn:
                params: tuple = ()
                where_extra = ""
                if self._entity_filter:
                    where_extra = " AND qbo_object_type = ?"
                    params = (self._entity_filter,)
                rows = conn.execute(
                    f"""
                    SELECT id, created_at, updated_at, entity_type, local_ref,
                           attempts,
                           COALESCE(last_error, error_message, '') AS err,
                           qbo_id, qbo_object_type, source_type, source_id,
                           sync_action,
                           COALESCE(request_json, '')  AS request_json,
                           COALESCE(response_json, '') AS response_json
                    FROM qbo_sync_log
                    WHERE status = 'failed'
                      AND resolved_at IS NULL
                      AND ignored_at IS NULL
                      {where_extra}
                    ORDER BY created_at DESC
                    LIMIT 500
                    """,
                    params,
                ).fetchall()
        except Exception as exc:
            rows = []
            QMessageBox.warning(self, "DB Error", f"Could not load failures: {exc}")

        from qbo.error_translator import translate as _translate
        from qbo.links import qbo_web_url
        from qbo.config import describe as _qbo_describe
        try:
            qbo_info = _qbo_describe() or {}
        except Exception:
            qbo_info = {}
        realm_id = qbo_info.get("realm_id") or qbo_info.get("company_id")
        environment = qbo_info.get("environment") or "sandbox"

        # Normalise + translate every row up-front.
        normalised: list[dict] = []
        cat_counter: dict[str, int] = {}
        # Hide internal/debug entity types from the UI — they're only
        # used by unit tests and should never reach end users.
        _HIDDEN_ENTITIES = {"mystery_thing", "test_entity", "debug"}
        for row in rows:
            if isinstance(row, tuple):
                (log_id, created_at, updated_at, entity, local_ref, attempts,
                 err, qbo_id, qbo_object_type, source_type, source_id,
                 sync_action, request_json, response_json) = row
            else:
                log_id = row["id"]
                created_at = row["created_at"]
                updated_at = row["updated_at"]
                entity = row["entity_type"]
                local_ref = row["local_ref"]
                attempts = row["attempts"]
                err = row["err"]
                qbo_id = row["qbo_id"]
                qbo_object_type = row["qbo_object_type"]
                source_type = row["source_type"]
                source_id = row["source_id"]
                sync_action = row["sync_action"]
                request_json = row["request_json"]
                response_json = row["response_json"]
            ent_norm = (entity or "").strip().lower()
            if ent_norm in _HIDDEN_ENTITIES:
                continue
            category, friendly = _translate(err)
            cat_counter[category] = cat_counter.get(category, 0) + 1
            normalised.append({
                "log_id": int(log_id),
                "first_failed": str(created_at or ""),
                "last_retry":   str(updated_at or ""),
                "entity": _pretty_entity(entity),
                "sync_action": str(sync_action or "").strip().lower() or
                               ("update" if (attempts or 0) > 0 else "create"),
                "local_ref": str(local_ref or "—"),
                "attempts": int(attempts or 0),
                "category": category,
                "friendly": friendly,
                "raw_error": str(err or ""),
                "request_json": str(request_json or ""),
                "response_json": str(response_json or ""),
                "qbo_id": qbo_id,
                "qbo_object_type": qbo_object_type,
                "source_type": source_type,
                "source_id": source_id,
            })

        self._all_rows = normalised
        self._realm_id = realm_id
        self._environment = environment

        # Rebuild category filter combo, preserving the current selection.
        current_cat = self._cat_combo.currentData()
        self._cat_combo.blockSignals(True)
        self._cat_combo.clear()
        self._cat_combo.addItem(f"All ({len(normalised)})", None)
        for cat in sorted(cat_counter):
            self._cat_combo.addItem(f"{cat} ({cat_counter[cat]})", cat)
        # Restore previous selection if still valid.
        if current_cat is not None:
            idx = self._cat_combo.findData(current_cat)
            if idx >= 0:
                self._cat_combo.setCurrentIndex(idx)
        self._cat_combo.blockSignals(False)

        self._render_rows()

    def _apply_category_filter(self) -> None:
        self._render_rows()

    def _render_rows(self) -> None:
        from qbo.links import qbo_web_url
        cat_filter = self._cat_combo.currentData() if hasattr(self, "_cat_combo") else None

        rows = [r for r in self._all_rows
                if cat_filter is None or r["category"] == cat_filter]
        self._table.setRowCount(0)
        if not rows:
            self._summary_lbl.setText("No failures match this filter.")
            self._table.setRowCount(1)
            self._table.setItem(0, self._COL_ERROR,
                                QTableWidgetItem("No unresolved failures 🎉"))
            return

        self._summary_lbl.setText(f"Showing {len(rows)} of {len(self._all_rows)}")
        for i, r in enumerate(rows):
            self._table.insertRow(i)

            # Checkbox (col 0)
            chk_item = QTableWidgetItem()
            chk_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            chk_item.setCheckState(Qt.CheckState.Unchecked)
            chk_item.setData(Qt.ItemDataRole.UserRole, r)
            self._table.setItem(i, self._COL_CHECK, chk_item)

            # Timestamps
            self._table.setItem(i, self._COL_FIRST,
                                QTableWidgetItem(_fmt_when(r["first_failed"])))
            last_retry = r["last_retry"] if r["attempts"] > 0 else "—"
            self._table.setItem(i, self._COL_LAST,
                                QTableWidgetItem(_fmt_when(last_retry)))
            self._table.setItem(i, self._COL_ENTITY,
                                QTableWidgetItem(r["entity"]))
            self._table.setItem(i, self._COL_ACTION,
                                QTableWidgetItem(r["sync_action"].title()))
            self._table.setItem(i, self._COL_LOCAL,
                                QTableWidgetItem(r["local_ref"]))

            # Category cell — coloured pill via background
            cat_item = QTableWidgetItem(r["category"])
            cat_item.setBackground(_cat_color(r["category"]))
            self._table.setItem(i, self._COL_CAT, cat_item)

            # Friendly error (raw available via Details button)
            err_item = QTableWidgetItem(r["friendly"])
            err_item.setToolTip(r["friendly"])
            self._table.setItem(i, self._COL_ERROR, err_item)

            self._table.setItem(i, self._COL_RETRIES,
                                QTableWidgetItem(str(r["attempts"])))

            # Details button — raw error + request/response payloads
            details_btn = QPushButton("Details…")
            details_btn.setToolTip("View the raw QBO error and payload.")
            details_btn.clicked.connect(
                lambda _c=False, row=r: self._show_raw_details(row)
            )
            self._table.setCellWidget(i, self._COL_DETAILS, details_btn)

            # Open Record (local DB)
            if r["source_type"] and r["source_id"]:
                open_btn = QPushButton("Open Record")
                open_btn.setToolTip(
                    f"Open the local {r['source_type']} #{r['source_id']}"
                )
                open_btn.clicked.connect(
                    lambda _c=False, st=r["source_type"], sid=r["source_id"]:
                    self._open_related_record(st, sid)
                )
                self._table.setCellWidget(i, self._COL_OPEN, open_btn)
            else:
                self._table.setItem(i, self._COL_OPEN, QTableWidgetItem("—"))

            # Open in QBO link
            link_url = None
            if (self._realm_id and r["qbo_object_type"] and r["qbo_id"]):
                try:
                    link_url = qbo_web_url(
                        str(self._realm_id), str(self._environment),
                        str(r["qbo_object_type"]), str(r["qbo_id"]),
                    )
                except Exception:
                    link_url = None
            if link_url:
                qbo_btn = QPushButton("Open in QBO")
                qbo_btn.setToolTip(link_url)
                qbo_btn.clicked.connect(
                    lambda _c=False, u=link_url: self._open_url(u)
                )
                self._table.setCellWidget(i, self._COL_QBO, qbo_btn)
            else:
                self._table.setItem(i, self._COL_QBO, QTableWidgetItem("—"))

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self._table.rowCount()):
            it = self._table.item(row, self._COL_CHECK)
            if it is not None and it.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                it.setCheckState(state)

    def _checked_log_ids(self) -> list[int]:
        ids: list[int] = []
        for row in range(self._table.rowCount()):
            it = self._table.item(row, self._COL_CHECK)
            if it is None:
                continue
            if it.checkState() == Qt.CheckState.Checked:
                data = it.data(Qt.ItemDataRole.UserRole) or {}
                lid = data.get("log_id")
                if lid is not None:
                    ids.append(int(lid))
        return ids

    def _open_related_record(self, source_type: str, source_id) -> None:
        """Best-effort: open the matching detail screen for source_type."""
        try:
            sid = int(source_id)
        except Exception:
            sid = source_id
        st = (source_type or "").lower()
        try:
            if st == "invoice":
                from jaks_inventory.ui.invoice_dialog import InvoiceDialog
                InvoiceDialog(invoice_id=sid, parent=self).exec()
                return
            if st in ("purchase_order", "po"):
                from jaks_inventory.ui.po_dialog import PODialog
                PODialog(po_id=sid, parent=self).exec()
                return
            if st == "bill":
                # Bills are surfaced via the PO dialog in this app.
                try:
                    from jaks_inventory.ui.po_dialog import PODialog
                    PODialog(po_id=sid, parent=self).exec()
                    return
                except Exception:
                    pass
            if st == "customer":
                from jaks_inventory.ui.customer_detail_dialog import CustomerDetailDialog
                CustomerDetailDialog(customer_id=sid, parent=self).exec()
                return
            if st == "vendor":
                from jaks_inventory.ui.vendor_dialog import VendorDialog
                VendorDialog(vendor_id=sid, parent=self).exec()
                return
            if st in ("quote", "estimate", "estimate_update"):
                from jaks_inventory.ui.quote_dialog import QuoteDialog
                QuoteDialog(quote_id=sid, parent=self).exec()
                return
            if st == "product":
                from jaks_inventory.ui.product_detail_dialog import ProductDetailDialog
                ProductDetailDialog(product_id=sid, parent=self).exec()
                return
            if st in ("payment", "credit_memo", "sales_receipt",
                      "refund_receipt", "bill_payment", "journal_entry"):
                QMessageBox.information(
                    self, "Open Record",
                    f"Related {st} #{sid} — open the relevant screen from "
                    "the side nav to view this record.",
                )
                return
        except Exception as exc:
            QMessageBox.warning(
                self, "Open Failed",
                f"Could not open {source_type} #{source_id}:\n{exc}",
            )
            return
        QMessageBox.information(
            self, "Open Record",
            f"No dedicated screen registered for type '{source_type}'.",
        )

    def _show_raw_details(self, row: dict) -> None:
        """Open a read-only dialog with the raw error + payloads."""
        from PySide6.QtWidgets import QPlainTextEdit
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Failure Details — log #{row.get('log_id')}")
        dlg.resize(820, 560)
        lay = QVBoxLayout(dlg)

        header = QLabel(
            f"<b>{row.get('entity', '—')}</b> — "
            f"{row.get('sync_action', '').title()} — "
            f"local ref <code>{row.get('local_ref', '—')}</code>"
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(header)

        cat_lbl = QLabel(
            f"<b>Category:</b> {row.get('category', '')}  "
            f"<b>Friendly:</b> {row.get('friendly', '')}"
        )
        cat_lbl.setWordWrap(True)
        cat_lbl.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(cat_lbl)

        def _section(title: str, text: str):
            lay.addWidget(QLabel(f"<b>{title}</b>"))
            box = QPlainTextEdit()
            box.setReadOnly(True)
            box.setPlainText(text or "(empty)")
            box.setMaximumHeight(160)
            lay.addWidget(box)

        _section("Raw QBO Error", row.get("raw_error", ""))
        _section("Request Payload (last attempt)", row.get("request_json", ""))
        _section("Response Body (last attempt)", row.get("response_json", ""))

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        dlg.exec()

    def _ignore_selected(self) -> None:
        log_ids = self._checked_log_ids()
        if not log_ids:
            QMessageBox.information(
                self, "Tick rows first",
                "Tick the checkbox on one or more rows, then click "
                "Mark Ignored.",
            )
            return
        confirm = QMessageBox.question(
            self, "Mark Ignored",
            f"Hide {len(log_ids)} failure(s) from the active list?\n\n"
            "The rows stay in the database for audit but won't appear "
            "in Retry All. This does NOT push anything to QuickBooks.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            from db.database import get_db
            from datetime import datetime
            now = datetime.utcnow().isoformat(timespec="seconds")
            placeholders = ",".join(["?"] * len(log_ids))
            with get_db() as conn:
                conn.execute(
                    f"UPDATE qbo_sync_log SET ignored_at = ? "
                    f"WHERE id IN ({placeholders})",
                    (now, *log_ids),
                )
                try:
                    conn.commit()
                except Exception:
                    pass
        except Exception as exc:
            QMessageBox.warning(self, "DB Error", f"Could not ignore: {exc}")
            return
        self._reload()

    def _open_url(self, url: str) -> None:
        try:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl(url))
        except Exception as exc:
            QMessageBox.warning(self, "Open failed", f"Could not open URL:\n{exc}")

    def _retry_selected(self) -> None:
        log_ids = self._checked_log_ids()
        if not log_ids:
            QMessageBox.information(
                self, "Tick rows first",
                "Tick the checkbox on one or more rows, then click Retry Selected.\n\n"
                "Tip: use ‘☑ Check All’ to tick everything visible, or "
                "‘↻↻ Retry All’ to retry everything without ticking.",
            )
            return
        self._do_retry(log_ids)

    def _retry_all(self) -> None:
        # Use the currently-visible (filtered) row set, not _all_rows, so
        # the category filter acts as a scope filter too.
        log_ids: list[int] = []
        for row in range(self._table.rowCount()):
            it = self._table.item(row, self._COL_CHECK)
            if it is None:
                continue
            data = it.data(Qt.ItemDataRole.UserRole) or {}
            lid = data.get("log_id")
            if lid is not None:
                log_ids.append(int(lid))
        if not log_ids:
            QMessageBox.information(
                self, "Nothing to retry",
                "There are no visible failures to retry.",
            )
            return
        confirm = QMessageBox.question(
            self, "Retry All",
            f"Retry all {len(log_ids)} visible failure(s)?\n\n"
            "Successful retries will be auto-resolved.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._do_retry(log_ids)

    def _do_retry(self, log_ids: list[int]) -> None:
        try:
            from qbo.retry_dispatch import retry_log_ids
            result = retry_log_ids(log_ids)
        except Exception as exc:
            QMessageBox.warning(
                self, "Retry failed",
                f"{type(exc).__name__}: {exc}",
            )
            return
        QMessageBox.information(
            self, "Retry complete",
            f"OK:          {result.get('ok', 0)}\n"
            f"Failed:      {result.get('failed', 0)}\n"
            f"Skipped:     {result.get('skipped', 0)}\n"
            f"Unsupported: {result.get('unsupported', 0)}",
        )
        self._reload()

    def _resolve_one(self, log_id: int) -> None:
        try:
            from db.database import get_db
            from datetime import datetime
            with get_db() as conn:
                conn.execute(
                    "UPDATE qbo_sync_log SET resolved_at = ? WHERE id = ?",
                    (datetime.utcnow().isoformat(timespec="seconds"), log_id),
                )
                try:
                    conn.commit()
                except Exception:
                    pass
        except Exception as exc:
            QMessageBox.warning(self, "DB Error", f"Could not resolve: {exc}")
            return
        self._reload()

    def _resolve_all(self) -> None:
        confirm = QMessageBox.question(
            self, "Mark All Resolved",
            "Mark every unresolved failure as resolved? "
            "They'll disappear from this list.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            from db.database import get_db
            from datetime import datetime
            with get_db() as conn:
                conn.execute(
                    "UPDATE qbo_sync_log SET resolved_at = ? "
                    "WHERE status = 'failed' AND resolved_at IS NULL",
                    (datetime.utcnow().isoformat(timespec="seconds"),),
                )
                try:
                    conn.commit()
                except Exception:
                    pass
        except Exception as exc:
            QMessageBox.warning(self, "DB Error", f"Could not resolve all: {exc}")
            return
        self._reload()


class _QBOPendingQueueDialog(QDialog):
    """Pending Queue control center.

    Lists rows from ``qbo_sync_queue`` (status=pending/processing/failed)
    so the operator can review and push them to QuickBooks. Each push
    flows through :mod:`qbo.sync_queue.drain` which records audit events
    in ``qbo_sync_events`` and mirrors failures into ``qbo_sync_log`` so
    the Failure Center stays the single source of truth for errors.
    """

    # Column constants
    _COL_CHECK    = 0
    _COL_CREATED  = 1
    _COL_LAST     = 2
    _COL_ENTITY   = 3
    _COL_ACTION   = 4
    _COL_DIR      = 5
    _COL_LOCAL    = 6
    _COL_QBO      = 7
    _COL_RISK     = 8
    _COL_STATUS   = 9
    _COL_MESSAGE  = 10
    _COL_DETAILS  = 11
    _COL_OPEN     = 12
    _NUM_COLS     = 13

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("QBO Pending Sync Queue")
        self.resize(1180, 560)
        layout = QVBoxLayout(self)

        hint = QLabel(
            "Local records staged for QuickBooks. Tick rows then use "
            "'Sync Selected' or 'Sync All Pending'. High-risk rows "
            "(inventory qty pushes, large invoices) ask for confirmation."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #555; font-style: italic;")
        layout.addWidget(hint)

        # Status filter row
        filt_row = QHBoxLayout()
        filt_row.addWidget(QLabel("Show:"))
        from PySide6.QtWidgets import QComboBox
        self._status_filter = QComboBox()
        self._status_filter.addItem("Active (pending + processing)",
                                    ["pending", "processing"])
        self._status_filter.addItem("Pending",    ["pending"])
        self._status_filter.addItem("Processing", ["processing"])
        self._status_filter.addItem("Failed",     ["failed"])
        self._status_filter.addItem("Synced",     ["synced"])
        self._status_filter.addItem("Ignored",    ["ignored"])
        self._status_filter.addItem("All", None)
        self._status_filter.currentIndexChanged.connect(
            lambda *_: self._reload())
        filt_row.addWidget(self._status_filter)
        filt_row.addStretch()
        self._summary_lbl = QLabel("")
        self._summary_lbl.setStyleSheet("color: #555;")
        filt_row.addWidget(self._summary_lbl)
        layout.addLayout(filt_row)

        self._table = QTableWidget(0, self._NUM_COLS)
        self._table.setHorizontalHeaderLabels([
            "", "Created", "Last Attempt", "Entity", "Action", "Direction",
            "Local Ref", "QBO ID", "Risk", "Status", "Message",
            "Details", "Open Record",
        ])
        hh = self._table.horizontalHeader()
        for c in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(self._COL_MESSAGE,
                                QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setDefaultSectionSize(28)
        layout.addWidget(self._table, 1)

        btn_row = QHBoxLayout()
        self._check_all_btn = QPushButton("☑ Check All")
        self._check_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        self._uncheck_btn = QPushButton("☐ Uncheck")
        self._uncheck_btn.clicked.connect(lambda: self._set_all_checked(False))
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self._reload)
        self._sync_sel_btn = QPushButton("↑ Sync Selected")
        self._sync_sel_btn.setStyleSheet(
            "QPushButton { background-color: #E3F2FD; font-weight: 600; "
            "padding: 4px 12px; }"
        )
        self._sync_sel_btn.clicked.connect(self._sync_selected)
        self._sync_all_btn = QPushButton("↑↑ Sync All Pending")
        self._sync_all_btn.setStyleSheet(
            "QPushButton { background-color: #C8E6C9; font-weight: 600; "
            "padding: 4px 12px; }"
        )
        self._sync_all_btn.setToolTip(
            "Push every pending record to QuickBooks. High-risk rows are "
            "confirmed individually."
        )
        self._sync_all_btn.clicked.connect(self._sync_all)
        self._ignore_btn = QPushButton("⊘ Mark Ignored")
        self._ignore_btn.setStyleSheet(
            "QPushButton { background-color: #FFF3E0; padding: 4px 12px; }"
        )
        self._ignore_btn.clicked.connect(self._ignore_selected)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.accept)

        btn_row.addWidget(self._check_all_btn)
        btn_row.addWidget(self._uncheck_btn)
        btn_row.addWidget(self._refresh_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._sync_sel_btn)
        btn_row.addWidget(self._sync_all_btn)
        btn_row.addWidget(self._ignore_btn)
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

        self._reload()

    # ------------------------------------------------------------------ data
    def _current_filter_statuses(self) -> list[str] | None:
        return self._status_filter.currentData()

    def _reload(self) -> None:
        try:
            from qbo import sync_queue as _sq
        except Exception as exc:
            QMessageBox.warning(self, "Module error",
                                f"qbo.sync_queue unavailable: {exc}")
            return
        statuses = self._current_filter_statuses()
        rows: list[dict] = []
        try:
            from db.database import get_db
            with get_db() as conn:
                if statuses is None:
                    raw = conn.execute(
                        "SELECT * FROM qbo_sync_queue "
                        "ORDER BY id DESC LIMIT 500"
                    ).fetchall()
                else:
                    placeholders = ",".join(["?"] * len(statuses))
                    raw = conn.execute(
                        f"SELECT * FROM qbo_sync_queue "
                        f"WHERE status IN ({placeholders}) "
                        f"ORDER BY id DESC LIMIT 500",
                        tuple(statuses),
                    ).fetchall()
                for r in raw:
                    if hasattr(r, "keys"):
                        rows.append(dict(r))
        except Exception as exc:
            QMessageBox.warning(self, "DB Error",
                                f"Could not load queue: {exc}")
            rows = []

        self._table.setRowCount(0)
        # Update summary
        counts = _sq.count_by_status()
        self._summary_lbl.setText(
            f"Pending: {counts.get('pending', 0)}   "
            f"Failed: {counts.get('failed', 0)}   "
            f"Synced: {counts.get('synced', 0)}"
        )

        if not rows:
            self._table.setRowCount(1)
            empty = QTableWidgetItem("Queue is empty 🎉")
            self._table.setItem(0, self._COL_LOCAL, empty)
            return

        for r in rows:
            ent_norm = (r.get("entity_type") or "").strip().lower()
            if ent_norm in {"mystery_thing", "test_entity", "debug"}:
                continue
            i = self._table.rowCount()
            self._table.insertRow(i)

            # Checkbox cell
            chk = QTableWidgetItem("")
            chk.setFlags(chk.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            chk.setCheckState(Qt.CheckState.Unchecked)
            chk.setData(Qt.ItemDataRole.UserRole, {
                "queue_id": int(r["id"]),
                "entity_type": r.get("entity_type"),
                "local_record_id": r.get("local_record_id"),
                "risk_level": r.get("risk_level"),
                "status": r.get("status"),
            })
            self._table.setItem(i, self._COL_CHECK, chk)

            self._table.setItem(i, self._COL_CREATED,
                                QTableWidgetItem(_fmt_when(r.get("created_at"))))
            self._table.setItem(i, self._COL_LAST,
                                QTableWidgetItem(_fmt_when(r.get("last_attempt_at"))))
            self._table.setItem(i, self._COL_ENTITY,
                                QTableWidgetItem(_pretty_entity(r.get("entity_type"))))
            self._table.setItem(i, self._COL_ACTION,
                                QTableWidgetItem(str(r.get("action") or "").title()))
            dir_label = "App → QBO" if (r.get("direction") or "") == "app_to_qbo" \
                else "QBO → App"
            self._table.setItem(i, self._COL_DIR, QTableWidgetItem(dir_label))

            local_ref = r.get("local_ref") or r.get("local_record_id") or "—"
            self._table.setItem(i, self._COL_LOCAL,
                                QTableWidgetItem(str(local_ref)))
            self._table.setItem(i, self._COL_QBO,
                                QTableWidgetItem(str(r.get("qbo_id") or "—")))

            risk = (r.get("risk_level") or "low").lower()
            risk_item = QTableWidgetItem(risk.title())
            risk_item.setBackground(self._risk_color(risk))
            self._table.setItem(i, self._COL_RISK, risk_item)

            status = (r.get("status") or "").lower()
            status_item = QTableWidgetItem(status.title())
            status_item.setBackground(self._status_color(status))
            self._table.setItem(i, self._COL_STATUS, status_item)

            msg_item = QTableWidgetItem(str(r.get("message") or ""))
            msg_item.setToolTip(str(r.get("message") or ""))
            self._table.setItem(i, self._COL_MESSAGE, msg_item)

            # Details button — popups payload + raw message.
            det_btn = QPushButton("Details")
            det_btn.clicked.connect(
                lambda _c=False, row=r: self._show_details(row))
            self._table.setCellWidget(i, self._COL_DETAILS, det_btn)

            # Open Record button — only when we have a local id.
            if r.get("local_record_id"):
                btn = QPushButton("Open")
                btn.clicked.connect(
                    lambda _c=False, et=r.get("entity_type"),
                    sid=r.get("local_record_id"):
                    self._open_related_record(et, sid)
                )
                self._table.setCellWidget(i, self._COL_OPEN, btn)
            else:
                self._table.setItem(i, self._COL_OPEN, QTableWidgetItem("—"))

    @staticmethod
    def _risk_color(risk: str):
        from PySide6.QtGui import QColor
        return {
            "critical": QColor("#FFAB91"),
            "high":     QColor("#FFCDD2"),
            "medium":   QColor("#FFE0B2"),
            "low":      QColor("#C8E6C9"),
        }.get(risk, QColor("#ECEFF1"))

    @staticmethod
    def _status_color(status: str):
        from PySide6.QtGui import QColor
        return {
            "pending":    QColor("#FFF9C4"),
            "processing": QColor("#BBDEFB"),
            "failed":     QColor("#FFCDD2"),
            "synced":     QColor("#C8E6C9"),
            "ignored":    QColor("#CFD8DC"),
        }.get(status, QColor("#ECEFF1"))

    # --------------------------------------------------------------- helpers
    def _set_all_checked(self, on: bool) -> None:
        st = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        for r in range(self._table.rowCount()):
            it = self._table.item(r, self._COL_CHECK)
            if it is not None:
                it.setCheckState(st)

    def _checked_rows(self) -> list[dict]:
        out: list[dict] = []
        for r in range(self._table.rowCount()):
            it = self._table.item(r, self._COL_CHECK)
            if it is None:
                continue
            if it.checkState() != Qt.CheckState.Checked:
                continue
            data = it.data(Qt.ItemDataRole.UserRole) or {}
            if data.get("queue_id"):
                out.append(data)
        return out

    def _all_pending(self) -> list[dict]:
        out: list[dict] = []
        for r in range(self._table.rowCount()):
            it = self._table.item(r, self._COL_CHECK)
            if it is None:
                continue
            data = it.data(Qt.ItemDataRole.UserRole) or {}
            if (data.get("status") or "").lower() == "pending":
                out.append(data)
        return out

    # ---------------------------------------------------------------- syncs
    def _confirm_high_risk(self, rows: list[dict]) -> list[int]:
        """Filter out any high/critical-risk rows the operator declines."""
        keep_ids: list[int] = []
        for d in rows:
            qid = int(d.get("queue_id"))
            risk = (d.get("risk_level") or "").lower()
            if risk == "critical":
                msg = (
                    "⚠  CRITICAL sync:\n\n"
                    f"  {d.get('entity_type')} (id={d.get('local_record_id')})\n\n"
                    "This may overwrite QBO quantity on hand.\n\n"
                    "Are you sure you want to proceed?"
                )
                ans = QMessageBox.warning(
                    self, "Confirm CRITICAL Sync", msg,
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if ans != QMessageBox.StandardButton.Yes:
                    continue
            elif risk == "high":
                msg = (
                    f"This is a HIGH-RISK sync:\n\n"
                    f"  {d.get('entity_type')} (id={d.get('local_record_id')})\n\n"
                    "High-risk syncs include invoice / bill / payment\n"
                    "pushes to QuickBooks. Continue?"
                )
                ans = QMessageBox.warning(
                    self, "Confirm High-Risk Sync", msg,
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No,
                )
                if ans != QMessageBox.StandardButton.Yes:
                    continue
            keep_ids.append(qid)
        return keep_ids

    def _sync_selected(self) -> None:
        rows = self._checked_rows()
        if not rows:
            QMessageBox.information(
                self, "Tick rows first",
                "Tick the checkbox on one or more rows, then click "
                "Sync Selected.",
            )
            return
        ids = self._confirm_high_risk(rows)
        if not ids:
            return
        self._do_drain(ids)

    def _sync_all(self) -> None:
        rows = self._all_pending()
        if not rows:
            QMessageBox.information(self, "Nothing to sync",
                                    "No pending rows in view.")
            return
        confirm = QMessageBox.question(
            self, "Sync All Pending",
            f"Push all {len(rows)} pending record(s) to QuickBooks now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        ids = self._confirm_high_risk(rows)
        if not ids:
            return
        self._do_drain(ids)

    def _do_drain(self, queue_ids: list[int]) -> None:
        try:
            from qbo.sync_queue import drain
            summary = drain(queue_ids)
        except Exception as exc:
            QMessageBox.warning(self, "Sync failed",
                                f"{type(exc).__name__}: {exc}")
            return
        QMessageBox.information(
            self, "Sync complete",
            f"Synced:  {summary.get('ok', 0)}\n"
            f"Failed:  {summary.get('failed', 0)}\n"
            f"Skipped: {summary.get('skipped', 0)}\n\n"
            "Failures (if any) have been recorded in the QBO Failure "
            "Center.",
        )
        self._reload()

    def _ignore_selected(self) -> None:
        rows = self._checked_rows()
        if not rows:
            QMessageBox.information(
                self, "Tick rows first",
                "Tick the checkbox on one or more rows, then click "
                "Mark Ignored.",
            )
            return
        ans = QMessageBox.question(
            self, "Mark Ignored",
            f"Mark {len(rows)} queue row(s) as ignored?\n\n"
            "They will stop appearing in Sync All and won't be pushed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            from qbo.sync_queue import mark_ignored
            mark_ignored([int(d["queue_id"]) for d in rows])
        except Exception as exc:
            QMessageBox.warning(self, "DB error", f"Could not ignore: {exc}")
            return
        self._reload()

    def _open_related_record(self, entity_type: str, local_id) -> None:
        # Reuse the failures dialog dispatcher for screen routing.
        _QBOFailuresDialog._open_related_record(self, entity_type, local_id)

    # -------------------------------------------------------------- details
    def _show_details(self, row: dict) -> None:
        """Modal popup with payload + raw message for a single row."""
        from PySide6.QtWidgets import (
            QDialog as _QD, QVBoxLayout as _QV, QLabel as _QL,
            QPlainTextEdit as _QPTE, QPushButton as _QPB,
        )
        dlg = _QD(self)
        dlg.setWindowTitle(
            f"Queue #{row.get('id')} — {_pretty_entity(row.get('entity_type'))}"
        )
        dlg.resize(700, 500)
        v = _QV(dlg)
        header = _QL(
            f"<b>Entity:</b> {_pretty_entity(row.get('entity_type'))}<br/>"
            f"<b>Action:</b> {row.get('action')}<br/>"
            f"<b>Local Ref:</b> {row.get('local_ref') or row.get('local_record_id') or '—'}<br/>"
            f"<b>QBO ID:</b> {row.get('qbo_id') or '—'}<br/>"
            f"<b>Risk:</b> {(row.get('risk_level') or '').title()}<br/>"
            f"<b>Status:</b> {(row.get('status') or '').title()}"
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(header)
        v.addWidget(_QL("<b>Message</b>"))
        msg = _QPTE(str(row.get("message") or ""))
        msg.setReadOnly(True)
        msg.setMaximumHeight(120)
        v.addWidget(msg)
        v.addWidget(_QL("<b>Payload</b>"))
        payload = _QPTE(str(row.get("payload_json") or ""))
        payload.setReadOnly(True)
        v.addWidget(payload, 1)
        close = _QPB("Close")
        close.clicked.connect(dlg.accept)
        v.addWidget(close)
        dlg.exec()


class _QBOSyncLogDialog(QDialog):
    """Append-only history view of every QBO sync attempt.

    Reads ``qbo_sync_events`` newest-first. Every state transition
    (processing, synced, failed, retry, ignored) writes one row, so the
    operator can audit exactly what happened and when. Raw payload /
    error JSON is reachable only via the per-row Details button to keep
    the table scannable.
    """

    _COL_TIME    = 0
    _COL_ENTITY  = 1
    _COL_LOCAL   = 2
    _COL_ACTION  = 3
    _COL_DIR     = 4
    _COL_RISK    = 5
    _COL_RESULT  = 6
    _COL_MESSAGE = 7
    _COL_DETAILS = 8
    _NUM_COLS    = 9

    _RESULT_FILTERS: list[tuple[str, str | None]] = [
        ("All",     None),
        ("Success", "synced"),
        ("Failed",  "failed"),
        ("Retry",   "retry"),
        ("Ignored", "ignored"),
    ]

    _DAY_FILTERS: list[tuple[str, int | None]] = [
        ("Any time",       None),
        ("Today",          1),
        ("Last 7 days",    7),
        ("Last 30 days",  30),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("QBO Sync Log")
        self.resize(1100, 560)
        layout = QVBoxLayout(self)

        hint = QLabel(
            "Append-only history of every QBO sync attempt. Use Details "
            "to inspect raw payload/response. Successful, failed, retried, "
            "and ignored attempts are all recorded."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #555; font-style: italic;")
        layout.addWidget(hint)

        from PySide6.QtWidgets import QComboBox
        filt_row = QHBoxLayout()
        filt_row.addWidget(QLabel("Result:"))
        self._result_combo = QComboBox()
        for label, val in self._RESULT_FILTERS:
            self._result_combo.addItem(label, val)
        self._result_combo.currentIndexChanged.connect(
            lambda *_: self._reload())
        filt_row.addWidget(self._result_combo)

        filt_row.addSpacing(12)
        filt_row.addWidget(QLabel("Window:"))
        self._day_combo = QComboBox()
        for label, val in self._DAY_FILTERS:
            self._day_combo.addItem(label, val)
        self._day_combo.currentIndexChanged.connect(
            lambda *_: self._reload())
        filt_row.addWidget(self._day_combo)

        filt_row.addStretch()
        self._summary_lbl = QLabel("")
        self._summary_lbl.setStyleSheet("color: #555;")
        filt_row.addWidget(self._summary_lbl)
        layout.addLayout(filt_row)

        self._table = QTableWidget(0, self._NUM_COLS)
        self._table.setHorizontalHeaderLabels([
            "Time", "Entity", "Local Ref", "Action", "Direction",
            "Risk", "Result", "Message", "Details",
        ])
        hh = self._table.horizontalHeader()
        for c in (0, 1, 2, 3, 4, 5, 6, 8):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(self._COL_MESSAGE,
                                QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setDefaultSectionSize(26)
        layout.addWidget(self._table, 1)

        btn_row = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self._reload)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._refresh_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

        self._reload()

    def _reload(self) -> None:
        try:
            from qbo.sync_queue import list_events
        except Exception as exc:
            QMessageBox.warning(self, "Module error",
                                f"qbo.sync_queue unavailable: {exc}")
            return
        status = self._result_combo.currentData()
        days = self._day_combo.currentData()
        try:
            rows = list_events(status=status, day_window=days, limit=1000)
        except Exception as exc:
            QMessageBox.warning(self, "DB Error",
                                f"Could not load events: {exc}")
            rows = []

        # Hide internal/debug entity types.
        _HIDDEN = {"mystery_thing", "test_entity", "debug"}
        rows = [r for r in rows
                if (r.get("entity_type") or "").strip().lower() not in _HIDDEN]
        self._summary_lbl.setText(f"{len(rows)} event(s)")

        self._table.setRowCount(0)
        for r in rows:
            i = self._table.rowCount()
            self._table.insertRow(i)
            self._table.setItem(
                i, self._COL_TIME,
                QTableWidgetItem(_fmt_when(r.get("created_at"))))
            self._table.setItem(
                i, self._COL_ENTITY,
                QTableWidgetItem(_pretty_entity(r.get("entity_type"))))
            self._table.setItem(
                i, self._COL_LOCAL,
                QTableWidgetItem(str(r.get("local_ref")
                                     or r.get("local_record_id")
                                     or "—")))
            self._table.setItem(
                i, self._COL_ACTION,
                QTableWidgetItem(str(r.get("action") or "").title()))
            dir_label = ("App → QBO"
                         if (r.get("direction") or "") == "app_to_qbo"
                         else "QBO → App")
            self._table.setItem(i, self._COL_DIR,
                                QTableWidgetItem(dir_label))

            risk = (r.get("risk_level") or "").lower()
            risk_item = QTableWidgetItem(risk.title() if risk else "—")
            if risk:
                risk_item.setBackground(
                    _QBOPendingQueueDialog._risk_color(risk))
            self._table.setItem(i, self._COL_RISK, risk_item)

            result = (r.get("status") or "").lower()
            result_item = QTableWidgetItem(result.title())
            result_item.setBackground(self._result_color(result))
            self._table.setItem(i, self._COL_RESULT, result_item)

            msg_item = QTableWidgetItem(str(r.get("message") or ""))
            msg_item.setToolTip(str(r.get("message") or ""))
            self._table.setItem(i, self._COL_MESSAGE, msg_item)

            det_btn = QPushButton("Details")
            det_btn.clicked.connect(
                lambda _c=False, row=r: self._show_event_details(row))
            self._table.setCellWidget(i, self._COL_DETAILS, det_btn)

    @staticmethod
    def _result_color(result: str):
        from PySide6.QtGui import QColor
        return {
            "synced":     QColor("#C8E6C9"),
            "failed":     QColor("#FFCDD2"),
            "retry":      QColor("#FFF59D"),
            "ignored":    QColor("#CFD8DC"),
            "processing": QColor("#BBDEFB"),
            "pending":    QColor("#FFF9C4"),
        }.get(result, QColor("#ECEFF1"))

    def _show_event_details(self, row: dict) -> None:
        """Modal popup with the raw_details / payload for one event."""
        from PySide6.QtWidgets import (
            QDialog as _QD, QVBoxLayout as _QV, QLabel as _QL,
            QPlainTextEdit as _QPTE, QPushButton as _QPB,
        )
        dlg = _QD(self)
        dlg.setWindowTitle(
            f"Event #{row.get('id')} — "
            f"{_pretty_entity(row.get('entity_type'))} / "
            f"{(row.get('status') or '').title()}"
        )
        dlg.resize(720, 540)
        v = _QV(dlg)
        local_disp = (row.get("local_ref") or row.get("local_record_id")
                      or "—")
        v.addWidget(_QL(
            f"<b>Time:</b> {row.get('created_at') or '—'}<br/>"
            f"<b>Entity:</b> {_pretty_entity(row.get('entity_type'))}<br/>"
            f"<b>Local Ref:</b> {local_disp}<br/>"
            f"<b>QBO ID:</b> {row.get('qbo_id') or '—'}<br/>"
            f"<b>Action:</b> {row.get('action') or '—'}<br/>"
            f"<b>Result:</b> {(row.get('status') or '').title()}"
        ))
        v.addWidget(_QL("<b>Message</b>"))
        msg = _QPTE(str(row.get("message") or ""))
        msg.setReadOnly(True)
        msg.setMaximumHeight(120)
        v.addWidget(msg)
        v.addWidget(_QL("<b>Raw details</b>"))
        raw = _QPTE(str(row.get("raw_details") or ""))
        raw.setReadOnly(True)
        v.addWidget(raw, 1)
        close = _QPB("Close")
        close.clicked.connect(dlg.accept)
        v.addWidget(close)
        dlg.exec()


class _QBOInventoryQtyPreviewDialog(QDialog):
    """Dry-run preview for ‘Push Inventory Qty → QBO’.

    Builds the same per-SKU diff the push would perform but issues no
    writes — just QBO lookups via :func:`qbo.client.search_by_sku`.
    Operator must click Confirm to proceed; Cancel returns to Settings
    without touching QuickBooks.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Push Inventory Qty — Preview")
        self.resize(820, 520)
        layout = QVBoxLayout(self)

        banner = QLabel(
            "⚠  This preview is a <b>dry run</b>. No data is written to "
            "QuickBooks until you click <b>Confirm Push</b>.<br/>"
            "Loading qty diff from QBO…"
        )
        banner.setStyleSheet(
            "QLabel { background-color: #FFF3E0; color: #E65100; "
            "padding: 8px; border: 1px solid #FFB74D; border-radius: 4px; }"
        )
        banner.setWordWrap(True)
        layout.addWidget(banner)
        self._banner = banner

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels([
            "SKU", "Local Qty", "QBO Qty", "Δ Change", "Result",
        ])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._table, 1)

        self._summary = QLabel("")
        self._summary.setStyleSheet("font-weight: 600; padding: 4px;")
        layout.addWidget(self._summary)

        btn_row = QHBoxLayout()
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)
        self._confirm_btn = QPushButton("Confirm Push →")
        self._confirm_btn.setStyleSheet(
            "QPushButton { background-color: #FFE0B2; color: #E65100; "
            "font-weight: 700; padding: 6px 16px; border: 2px solid #FB8C00; }"
            "QPushButton:disabled { color: #999; border-color: #ccc; }"
        )
        self._confirm_btn.setEnabled(False)
        self._confirm_btn.clicked.connect(self.accept)
        btn_row.addStretch()
        btn_row.addWidget(self._cancel_btn)
        btn_row.addWidget(self._confirm_btn)
        layout.addLayout(btn_row)

        # Defer the QBO query so the dialog can paint first.
        from PySide6.QtCore import QTimer
        QTimer.singleShot(50, self._load_preview)

    def _load_preview(self) -> None:
        # Gather local inventory snapshot
        from db.database import get_db
        try:
            with get_db() as conn:
                rows = conn.execute(
                    """SELECT sku, qty_on_hand FROM products
                       WHERE product_type = 'inventoried'
                         AND COALESCE(is_active, 1) = 1
                         AND sku IS NOT NULL AND sku != ''
                       ORDER BY sku"""
                ).fetchall()
        except Exception as exc:
            self._banner.setText(
                f"<b>Could not load local products:</b> {exc}"
            )
            return

        local: list[tuple[str, float]] = []
        for r in rows:
            sku = r[0] if isinstance(r, tuple) else r["sku"]
            qty = r[1] if isinstance(r, tuple) else r["qty_on_hand"]
            local.append((str(sku), float(qty or 0)))

        if not local:
            self._banner.setText("No inventoried products to push.")
            self._summary.setText("0 records — nothing to do.")
            return

        # Query QBO for each SKU. Mock mode returns deterministic stubs;
        # live mode hits the SDK.
        try:
            from qbo.client import get_qbo_client
            client = get_qbo_client()
        except Exception as exc:
            self._banner.setText(
                f"<b>Could not connect to QBO:</b> {exc}"
            )
            return

        diffs: list[dict] = []
        unchanged = missing = changed = 0
        for sku, local_qty in local:
            try:
                existing = client.search_by_sku(sku)
            except Exception as exc:
                diffs.append({
                    "sku": sku, "local": local_qty, "qbo": None,
                    "delta": None, "result": f"Lookup failed: {exc}",
                })
                continue
            if not existing:
                missing += 1
                diffs.append({
                    "sku": sku, "local": local_qty, "qbo": None,
                    "delta": None,
                    "result": "Will be skipped (not in QBO)",
                })
                continue
            qbo_qty_raw = (
                existing.get("qty_on_hand")
                if isinstance(existing, dict) else None
            )
            try:
                qbo_qty = float(qbo_qty_raw) if qbo_qty_raw is not None else None
            except Exception:
                qbo_qty = None
            if qbo_qty is None:
                diffs.append({
                    "sku": sku, "local": local_qty, "qbo": None,
                    "delta": None,
                    "result": "QBO qty unknown — will overwrite",
                })
                changed += 1
                continue
            delta = local_qty - qbo_qty
            if abs(delta) < 0.0001:
                unchanged += 1
                diffs.append({
                    "sku": sku, "local": local_qty, "qbo": qbo_qty,
                    "delta": 0.0, "result": "No change",
                })
            else:
                changed += 1
                diffs.append({
                    "sku": sku, "local": local_qty, "qbo": qbo_qty,
                    "delta": delta,
                    "result": "Will overwrite QBO qty",
                })

        # Render
        self._table.setRowCount(len(diffs))
        for i, d in enumerate(diffs):
            self._table.setItem(i, 0, QTableWidgetItem(d["sku"]))
            self._table.setItem(i, 1, QTableWidgetItem(
                f"{d['local']:g}" if d['local'] is not None else "—"))
            self._table.setItem(i, 2, QTableWidgetItem(
                f"{d['qbo']:g}" if d['qbo'] is not None else "—"))
            if d["delta"] is None:
                delta_text = "—"
            elif d["delta"] > 0:
                delta_text = f"+{d['delta']:g}"
            elif d["delta"] < 0:
                delta_text = f"{d['delta']:g}"
            else:
                delta_text = "0"
            self._table.setItem(i, 3, QTableWidgetItem(delta_text))
            res_item = QTableWidgetItem(d["result"])
            # Tint rows by outcome.
            from PySide6.QtGui import QColor
            if d["result"].startswith("Will overwrite"):
                res_item.setBackground(QColor("#FFF3E0"))
            elif d["result"].startswith("Will be skipped"):
                res_item.setBackground(QColor("#E0E0E0"))
            elif d["result"] == "No change":
                res_item.setBackground(QColor("#DCEDC8"))
            elif d["result"].startswith("Lookup failed"):
                res_item.setBackground(QColor("#FFCDD2"))
            self._table.setItem(i, 4, res_item)

        self._banner.setText(
            "⚠  This preview is a <b>dry run</b>. No data is written to "
            "QuickBooks until you click <b>Confirm Push</b>."
        )
        self._summary.setText(
            f"Total: {len(diffs)}    "
            f"Will overwrite: {changed}    "
            f"No change: {unchanged}    "
            f"Skipped (missing in QBO): {missing}"
        )
        # Only enable Confirm if there's something to do.
        self._confirm_btn.setEnabled(changed > 0)


class _QBOIntegrationsWorker(QThread):
    """Background worker for QBO connect + sync operations.

    Designed so the Settings screen never blocks the UI thread while
    OAuth waits for the browser callback or while bulk pushes loop
    through hundreds of invoices.
    """

    finished_with_result = Signal(dict)

    def __init__(self, parent, *, _kind: str) -> None:
        super().__init__(parent)
        self._kind = _kind

    def run(self) -> None:  # noqa: D401 — QThread API
        try:
            result = self._dispatch()
        except Exception as exc:  # pragma: no cover — UI surfaces it
            result = {"error": f"{type(exc).__name__}: {exc}"}
        self.finished_with_result.emit(result)

    def _dispatch(self) -> dict:
        if self._kind == "connect":
            from qbo.oauth import start_oauth_flow
            return start_oauth_flow()
        if self._kind == "customers":
            from qbo.customers import sync_all_customers
            return sync_all_customers(active_only=True)
        if self._kind == "import_customers":
            from qbo.customers import import_customers_from_qbo
            return import_customers_from_qbo()
        if self._kind == "vendors":
            from qbo.vendors import sync_all_vendors
            return sync_all_vendors(active_only=True)
        if self._kind == "import_vendors":
            from qbo.vendors import import_vendors_from_qbo
            return import_vendors_from_qbo()
        if self._kind == "import_invoices":
            from qbo.invoices import import_qbo_invoices_to_db
            return import_qbo_invoices_to_db()
        if self._kind == "import_payments":
            from qbo.payments import import_qbo_payments_to_db
            return import_qbo_payments_to_db()
        if self._kind == "import_credits":
            from qbo.credit_memos import import_qbo_credit_memos_to_db
            return import_qbo_credit_memos_to_db()
        if self._kind == "import_bills":
            from qbo.bills import import_qbo_bills_to_db
            return import_qbo_bills_to_db()
        if self._kind == "import_pos":
            from qbo.purchase_orders import import_qbo_purchase_orders_to_db
            return import_qbo_purchase_orders_to_db()
        if self._kind == "import_estimates":
            from qbo.estimates import import_qbo_estimates_to_db
            return import_qbo_estimates_to_db()
        if self._kind == "import_sales_receipts":
            from qbo.sales_receipts import import_qbo_sales_receipts_to_db
            return import_qbo_sales_receipts_to_db()
        if self._kind == "import_refund_receipts":
            from qbo.refund_receipts import import_qbo_refund_receipts_to_db
            return import_qbo_refund_receipts_to_db()
        if self._kind == "import_bill_payments":
            from qbo.bill_payments import import_qbo_bill_payments_to_db
            return import_qbo_bill_payments_to_db()
        if self._kind == "push_sales_receipts":
            return self._push_sales_receipts_to_qbo()
        if self._kind == "push_refunds":
            return self._push_refunds_to_qbo()
        if self._kind == "push_bill_payments":
            return self._push_bill_payments_to_qbo()
        if self._kind == "import_tax_codes":
            from qbo.reference_data import import_qbo_tax_codes_to_db
            return import_qbo_tax_codes_to_db()
        if self._kind == "import_classes":
            from qbo.reference_data import import_qbo_classes_to_db
            return import_qbo_classes_to_db()
        if self._kind == "import_locations":
            from qbo.reference_data import import_qbo_locations_to_db
            return import_qbo_locations_to_db()
        if self._kind == "invoices":
            return self._sync_invoices()
        if self._kind == "payments":
            return self._sync_payments()
        if self._kind == "journal_entries":
            return self._sync_journal_entries()
        if self._kind == "sync_products":
            return self._sync_products_to_qbo()
        if self._kind == "import_products_qbo":
            return self._import_products_from_qbo()
        if self._kind == "push_inventory_qty":
            return self._push_inventory_qty_to_qbo()
        return {"error": f"Unknown sync kind: {self._kind}"}

    # ── Per-entity sync helpers ─────────────────────────────────────
    def _eligible_invoices(self) -> list[dict]:
        from db import get_db
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id FROM invoices "
                "WHERE COALESCE(qbo_exported, 0) = 0 "
                "  AND COALESCE(status, '') NOT IN ('void', 'draft')"
            ).fetchall()
        ids = [r["id"] if hasattr(r, "keys") else r[0] for r in rows]
        from db.inventory import get_invoice
        out = []
        for inv_id in ids:
            inv = get_invoice(int(inv_id))
            if inv:
                out.append(inv)
        return out

    # ── Product sync helpers ────────────────────────────────────────

    def _sync_products_to_qbo(self) -> dict:
        """Push all active local products to QBO as Items via the
        QBOIntegrationService (single source of truth for create/update
        logic and account mapping).
        """
        from qbo.integration import QBOIntegrationService
        try:
            svc = QBOIntegrationService()
            result = svc.sync_products_from_db()
        except Exception as exc:
            return {"created": 0, "updated": 0, "skipped": 0,
                    "failed": 0, "errors": [f"sync_products_from_db: {exc}"]}
        return {
            "created": result.created,
            "updated": result.updated,
            "skipped": result.skipped,
            "failed": len(result.errors),
            "errors": result.errors,
        }

    def _sync_products_to_qbo_LEGACY(self) -> dict:
        """LEGACY: direct client.* path (kept for reference, not called).

        - inventoried products → Inventory type
        - service products     → Service type
        Creates new items where the SKU is absent; updates existing ones.
        """
        from qbo.client import get_qbo_client
        from db.database import get_db

        client = get_qbo_client()
        created = updated = skipped = failed = 0
        errors: list[str] = []

        with get_db() as conn:
            rows = conn.execute(
                """SELECT sku, title, selling_price, cost, qty_on_hand,
                          invoice_description, product_type
                   FROM products
                   WHERE COALESCE(is_active, 1) = 1
                     AND sku IS NOT NULL AND sku != ''"""
            ).fetchall()        

        for row in rows:
            if isinstance(row, dict):
                sku, title, price, cost, qty, desc, ptype = (
                    row["sku"], row["title"], row["selling_price"],
                    row["cost"], row["qty_on_hand"],
                    row["invoice_description"], row["product_type"],
                )
            else:
                sku, title, price, cost, qty, desc, ptype = row

            if not sku:
                skipped += 1
                continue

            try:
                existing = client.search_by_sku(sku)
                description = str(desc or title or "")
                if existing:
                    result = client.update_item(
                        existing["id"],
                        name=str(title or sku)[:100],
                        price=float(price or 0),
                        cost=float(cost or 0),
                        description=description[:4000],
                    )
                    if result:
                        updated += 1
                    else:
                        failed += 1
                        errors.append(f"{sku}: update returned None")
                else:
                    result = client.create_item(
                        sku=sku,
                        name=str(title or sku)[:100],
                        price=float(price or 0),
                        cost=float(cost or 0),
                        qty_on_hand=float(qty or 0) if ptype == "inventoried" else 0.0,
                        description=description[:4000],
                    )
                    if result:
                        created += 1
                    else:
                        failed += 1
                        errors.append(f"{sku}: create returned None")
            except Exception as exc:
                failed += 1
                errors.append(f"{sku}: {exc}")

        return {"created": created, "updated": updated,
                "skipped": skipped, "failed": failed, "errors": errors}

    def _import_products_from_qbo(self) -> dict:
        """Fetch QBO Items and import / link them against local products.

        Behaviour per QBO item:

        * Matched (SKU exists locally) → set ``products.qbo_id`` and
          stamp ``qbo_synced_at`` so the matched count is also a useful
          link operation. Pricing / qty are NOT overwritten (local is
          authoritative).
        * Unmatched → insert a minimal product row using the QBO
          name/price/cost/description, stamped with ``qbo_id``.
        """
        from qbo.client import get_qbo_client
        from db.database import get_db
        from datetime import datetime

        client = get_qbo_client()
        errors: list[str] = []

        try:
            qbo_items = client.fetch_all_items()
        except Exception as exc:
            return {"error": str(exc)}

        if not qbo_items:
            return {"total": 0, "matched": 0, "unmatched": 0,
                    "imported": 0, "linked": 0, "errors": []}

        # Build a map of existing local SKU → (id, qbo_id) so we can detect
        # whether the row needs a fresh qbo_id link.
        with get_db() as conn:
            local_rows = conn.execute(
                "SELECT id, sku, qbo_id FROM products WHERE sku IS NOT NULL"
            ).fetchall()
        by_sku: dict[str, tuple[int, str | None]] = {}
        for r in local_rows:
            if hasattr(r, "keys"):
                by_sku[(r["sku"] or "").upper()] = (r["id"], r["qbo_id"])
            else:
                by_sku[(r[0] or "").upper() if False else (r[1] or "").upper()] = (
                    r[0], r[2]
                )

        matched = unmatched = imported = linked = 0
        now_iso = datetime.now().isoformat()

        with get_db() as conn:
            for item in qbo_items:
                qbo_sku_raw = (item.get("sku") or "").strip()
                qbo_item_id = str(item.get("id") or "").strip()
                if not qbo_sku_raw:
                    # Skip QBO items without a SKU — those are usually
                    # service rows the user wouldn't want to mirror.
                    unmatched += 1
                    continue

                local_key = qbo_sku_raw.upper()
                if local_key in by_sku:
                    matched += 1
                    local_id, existing_qbo_id = by_sku[local_key]
                    if qbo_item_id and existing_qbo_id != qbo_item_id:
                        try:
                            conn.execute(
                                "UPDATE products SET qbo_id = ?, "
                                "qbo_synced_at = ? WHERE id = ?",
                                (qbo_item_id, now_iso, local_id),
                            )
                            linked += 1
                        except Exception as exc:
                            errors.append(f"{qbo_sku_raw}: link failed — {exc}")
                    # B6: additive merge — fill empty local fields from
                    # QBO, never overwrite non-empty (locally curated
                    # title/description/cost/price beats QBO defaults).
                    try:
                        local_full = conn.execute(
                            "SELECT title, description_html, cost, "
                            "selling_price FROM products WHERE id = ?",
                            (local_id,),
                        ).fetchone()
                        if local_full:
                            def _g(row, k, i):
                                return row[k] if hasattr(row, "keys") else row[i]
                            l_title = (_g(local_full, "title", 0) or "").strip()
                            l_desc = (_g(local_full, "description_html", 1) or "").strip()
                            l_cost = float(_g(local_full, "cost", 2) or 0)
                            l_price = float(_g(local_full, "selling_price", 3) or 0)
                            patches: list[tuple[str, object]] = []
                            if not l_title and (item.get("name") or "").strip():
                                patches.append(("title", item["name"][:255]))
                            if not l_desc and (item.get("description") or "").strip():
                                patches.append((
                                    "description_html", item["description"][:4000]))
                            if l_cost == 0 and float(item.get("cost") or 0) > 0:
                                patches.append(("cost", float(item["cost"])))
                            if l_price == 0 and float(item.get("price") or 0) > 0:
                                patches.append((
                                    "selling_price", float(item["price"])))
                            if patches:
                                sets = ", ".join(f"{c} = ?" for c, _ in patches)
                                vals = [v for _, v in patches] + [local_id]
                                conn.execute(
                                    f"UPDATE products SET {sets}, "
                                    f"updated_at = datetime('now') WHERE id = ?",
                                    vals,
                                )
                    except Exception as exc:
                        errors.append(f"{qbo_sku_raw}: merge skipped — {exc}")
                    continue

                unmatched += 1
                qbo_type = (item.get("type") or "Inventory")
                ptype = "inventoried" if qbo_type == "Inventory" else "service"
                try:
                    conn.execute(
                        """INSERT INTO products
                              (sku, title, selling_price, cost,
                               description_html, qty_on_hand,
                               product_type, stage,
                               qbo_id, qbo_synced_at,
                               created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, 1,
                                   ?, ?,
                                   datetime('now'), datetime('now'))""",
                        (
                            qbo_sku_raw,
                            (item.get("name") or qbo_sku_raw)[:255],
                            float(item.get("price") or 0),
                            float(item.get("cost") or 0),
                            (item.get("description") or "")[:4000],
                            int(item.get("qty_on_hand") or 0),
                            ptype,
                            qbo_item_id or None,
                            now_iso if qbo_item_id else None,
                        ),
                    )
                    imported += 1
                    by_sku[local_key] = (None, qbo_item_id)
                except Exception as exc:
                    errors.append(f"{qbo_sku_raw}: {exc}")
            conn.commit()

        return {
            "total": len(qbo_items),
            "matched": matched,
            "unmatched": unmatched,
            "imported": imported,
            "linked": linked,
            "errors": errors,
        }

    def _push_inventory_qty_to_qbo(self) -> dict:
        """Push qty_on_hand for all inventoried products to QBO.

        Finds each local inventoried product in QBO by SKU and calls
        ``adjust_quantity`` (which sets qty_on_hand via update_item).
        """
        from qbo.client import get_qbo_client
        from db.database import get_db

        client = get_qbo_client()
        created = updated = skipped = failed = 0
        errors: list[str] = []

        with get_db() as conn:
            rows = conn.execute(
                """SELECT sku, qty_on_hand FROM products
                   WHERE product_type = 'inventoried'
                     AND COALESCE(is_active, 1) = 1
                     AND sku IS NOT NULL AND sku != ''"""
            ).fetchall()

        for row in rows:
            sku = (row[0] if isinstance(row, tuple) else row["sku"])
            qty = (row[1] if isinstance(row, tuple) else row["qty_on_hand"]) or 0

            try:
                existing = client.search_by_sku(sku)
                if not existing:
                    skipped += 1
                    continue
                result = client.adjust_quantity(existing["id"], float(qty))
                if result:
                    updated += 1
                else:
                    failed += 1
                    errors.append(f"{sku}: adjust_quantity returned None")
            except Exception as exc:
                failed += 1
                errors.append(f"{sku}: {exc}")

        return {"created": created, "updated": updated,
                "skipped": skipped, "failed": failed, "errors": errors}

    def _sync_invoices(self) -> dict:
        from qbo.push import push_invoices_to_qbo
        from datetime import datetime
        from db import get_db
        invs = self._eligible_invoices()
        if not invs:
            return {"ok": 0, "skipped": 0, "failed": 0}

        def _stamp(invoice_id: int, qbo_id: str) -> None:
            try:
                with get_db() as conn:
                    conn.execute(
                        "UPDATE invoices "
                        "SET qbo_invoice_id=%s, qbo_exported=1, "
                        "    qbo_exported_at=%s "
                        "WHERE id=%s",
                        (qbo_id, datetime.utcnow().isoformat(), int(invoice_id)),
                    )
                    conn.commit()
            except Exception:
                pass

        return push_invoices_to_qbo(invs, on_success=_stamp)

    def _sync_payments(self) -> dict:
        from qbo.push import push_payments_to_qbo
        from db import get_db
        from db.inventory import get_paid_invoices_for_export
        # Payments are tracked on the invoice row (amount_paid) and in
        # ``invoice_payments``; there is no top-level ``payments`` table.
        # Pull paid invoices that haven't been pushed as ReceivePayments
        # yet (qbo_payment_id is NULL/empty).
        pays = [
            p for p in get_paid_invoices_for_export()
            if not (p.get("qbo_payment_id") or "").strip()
        ]
        if not pays:
            return {"ok": 0, "skipped": 0, "failed": 0}

        def _stamp(invoice_id: int, qbo_payment_id: str) -> None:
            try:
                with get_db() as conn:
                    conn.execute(
                        "UPDATE invoices SET qbo_payment_id=%s WHERE id=%s",
                        (qbo_payment_id, int(invoice_id)),
                    )
                    conn.commit()
            except Exception:
                pass

        return push_payments_to_qbo(pays, on_success=_stamp)

    def _sync_journal_entries(self) -> dict:
        from qbo.journal_entries import (
            push_journal_entries_for_invoices, stamp_invoice_je,
        )
        invs = self._eligible_invoices()
        # Allow re-pushing JE even if invoice already QBO-exported; what
        # matters is whether the JE id is set.
        from db import get_db
        from db.inventory import get_invoice
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id FROM invoices "
                "WHERE COALESCE(qbo_journal_entry_id, '') = '' "
                "  AND COALESCE(status, '') NOT IN ('void', 'draft')"
            ).fetchall()
        ids = [r["id"] if hasattr(r, "keys") else r[0] for r in rows]
        invs = [get_invoice(int(i)) for i in ids]
        invs = [i for i in invs if i]
        if not invs:
            return {"ok": 0, "skipped": 0, "failed": 0}
        return push_journal_entries_for_invoices(invs, on_success=stamp_invoice_je)

    # ── Phase C-next push helpers ────────────────────────────────────
    def _push_sales_receipts_to_qbo(self) -> dict:
        """Push invoices flagged invoice_kind='sales_receipt' to QBO."""
        from qbo.push import push_sales_receipts_to_qbo
        from db import get_db
        from db.inventory import get_invoice
        from datetime import datetime
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id FROM invoices "
                "WHERE COALESCE(invoice_kind, '') = 'sales_receipt' "
                "  AND COALESCE(qbo_sales_receipt_id, '') = '' "
                "  AND COALESCE(status, '') NOT IN ('void', 'draft')"
            ).fetchall()
        ids = [r["id"] if hasattr(r, "keys") else r[0] for r in rows]
        invs = [get_invoice(int(i)) for i in ids]
        invs = [i for i in invs if i]
        if not invs:
            return {"ok": 0, "skipped": 0, "failed": 0}

        def _stamp(inv_id: int, qbo_id: str) -> None:
            try:
                with get_db() as conn:
                    conn.execute(
                        "UPDATE invoices "
                        "SET qbo_sales_receipt_id=%s, qbo_exported=1, "
                        "    qbo_exported_at=%s "
                        "WHERE id=%s",
                        (qbo_id, datetime.utcnow().isoformat(), int(inv_id)),
                    )
                    conn.commit()
            except Exception:
                pass

        return push_sales_receipts_to_qbo(invs, on_success=_stamp)

    def _push_refunds_to_qbo(self) -> dict:
        """Push customer_credits flagged as cash refunds to QBO."""
        from qbo.push import push_refunds_to_qbo
        from db import get_db
        with get_db() as conn:
            rows = conn.execute(
                "SELECT cc.*, c.name AS customer_name, "
                "       c.company AS customer_company "
                "FROM customer_credits cc "
                "LEFT JOIN customers c ON c.id = cc.customer_id "
                "WHERE COALESCE(cc.source_type, '') = 'refund' "
                "  AND COALESCE(cc.qbo_refund_receipt_id, '') = '' "
                "  AND COALESCE(cc.status, '') != 'voided' "
                "  AND COALESCE(cc.amount, 0) > 0"
            ).fetchall()
        refunds = [dict(r) if hasattr(r, "keys") else dict(enumerate(r))
                   for r in rows]
        if not refunds:
            return {"ok": 0, "skipped": 0, "failed": 0}

        def _stamp(cid: int, qbo_id: str) -> None:
            try:
                with get_db() as conn:
                    conn.execute(
                        "UPDATE customer_credits "
                        "SET qbo_refund_receipt_id=%s WHERE id=%s",
                        (qbo_id, int(cid)),
                    )
                    conn.commit()
            except Exception:
                pass

        return push_refunds_to_qbo(refunds, on_success=_stamp)

    def _push_bill_payments_to_qbo(self) -> dict:
        """Push local bill_payments to QBO as BillPayments."""
        from qbo.push import push_bill_payments_to_qbo
        from db import get_db
        with get_db() as conn:
            rows = conn.execute(
                "SELECT bp.*, v.name AS vendor_name, "
                "       po.qbo_bill_id AS bill_qbo_id "
                "FROM bill_payments bp "
                "LEFT JOIN vendors v ON v.id = bp.vendor_id "
                "LEFT JOIN purchase_orders po ON po.id = bp.purchase_order_id "
                "WHERE COALESCE(bp.qbo_bill_payment_id, '') = '' "
                "  AND COALESCE(bp.status, '') != 'voided' "
                "  AND COALESCE(bp.amount, 0) > 0"
            ).fetchall()
        payments = []
        for r in rows:
            d = dict(r) if hasattr(r, "keys") else dict(enumerate(r))
            if d.get("bill_qbo_id"):
                d["bill_qbo_ids"] = [str(d["bill_qbo_id"])]
            payments.append(d)
        if not payments:
            return {"ok": 0, "skipped": 0, "failed": 0}

        def _stamp(pid: int, qbo_id: str) -> None:
            try:
                with get_db() as conn:
                    conn.execute(
                        "UPDATE bill_payments "
                        "SET qbo_bill_payment_id=%s WHERE id=%s",
                        (qbo_id, int(pid)),
                    )
                    conn.commit()
            except Exception:
                pass

        return push_bill_payments_to_qbo(payments, on_success=_stamp)