"""QBO Export Screen — export invoices, bills, customer payments, and
credit memos as QBO-Online-importable CSV files.

CSV writers live in :mod:`qbo.csv_export`. This module is just the Qt
wrapper: it loads candidate rows from the DB, lets the user pick a
selection, and hands the rows to the writer.

IIF support has been removed (the previous implementation was
structurally broken and IIF is QB-Desktop-only / deprecated for QBO
Online).
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from db import (
    get_unexported_invoices,
    get_unexported_bills,
    mark_invoices_exported,
    mark_pos_exported,
    log_qbo_export,
    get_export_history,
    get_invoice,
    get_purchase_order,
    get_paid_invoices_for_export,
    get_credit_memos_for_export,
)
from qbo.csv_export import (
    write_invoices_csv,
    write_bills_csv,
    write_payments_csv,
    write_credit_memos_csv,
)
from qbo.csv_variants import (
    CsvVariant,
    VARIANT_LABELS,
    get_default_variant,
    set_default_variant,
    native_support_note,
    SUPPORTED_NATIVELY,
)
from qbo.validate import validate_for_export, ValidationReport


def _date_str(qdate: QDate) -> str:
    return qdate.toString("yyyy-MM-dd")


def _ro_item(text) -> QTableWidgetItem:
    it = QTableWidgetItem(str(text) if text is not None else "")
    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return it


class QBOExportScreen(QWidget):
    """QBO file-export screen — CSV-only, four tabs + history."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._invoices: list[dict] = []
        self._bills: list[dict] = []
        self._payments: list[dict] = []
        self._credits: list[dict] = []

        tabs = QTabWidget()
        tabs.addTab(self._build_invoice_tab(), "Invoices")
        tabs.addTab(self._build_bill_tab(), "Bills / POs")
        tabs.addTab(self._build_payment_tab(), "Customer Payments")
        tabs.addTab(self._build_credit_tab(), "Credit Memos")
        tabs.addTab(self._build_history_tab(), "Export History")
        tabs.addTab(self._build_audit_tab(), "🔍 Audit Log")

        layout = QVBoxLayout()
        layout.addWidget(QLabel(
            "QBO Export — CSV files or live API push to QuickBooks Online"
        ))
        # D5 — health banner. Skip connect on first paint so the screen
        # opens instantly; user can click Refresh for a live check.
        self._health_label = QLabel("")
        self._health_label.setStyleSheet(
            "padding: 4px 8px; background: #f5f5f5; "
            "border: 1px solid #ddd; border-radius: 3px;"
        )
        health_row = QHBoxLayout()
        health_row.addWidget(self._health_label, 1)
        refresh_health = QPushButton("Refresh QBO Status")
        refresh_health.clicked.connect(lambda: self._refresh_health(skip_connect=False))
        health_row.addWidget(refresh_health)
        layout.addLayout(health_row)

        # E1 — CSV variant selector + importer-awareness banner.
        variant_row = QHBoxLayout()
        variant_row.addWidget(QLabel("CSV variant:"))
        self._variant_combo = QComboBox()
        for v in (
            CsvVariant.QBO_ONLINE,
            CsvVariant.SAASANT,
            CsvVariant.TRANSACTION_PRO,
            CsvVariant.GENERIC,
        ):
            self._variant_combo.addItem(VARIANT_LABELS[v], v.value)
        # Restore saved preference (defaults to GENERIC if unset).
        try:
            saved = get_default_variant()
            idx = self._variant_combo.findData(saved.value)
            if idx >= 0:
                self._variant_combo.setCurrentIndex(idx)
        except Exception:
            pass
        self._variant_combo.currentIndexChanged.connect(self._on_variant_changed)
        variant_row.addWidget(self._variant_combo)
        self._variant_note = QLabel("")
        self._variant_note.setWordWrap(True)
        self._variant_note.setStyleSheet(
            "padding: 4px 8px; background: #fffde7; "
            "border: 1px solid #f0d000; border-radius: 3px;"
        )
        variant_row.addWidget(self._variant_note, 1)
        layout.addLayout(variant_row)
        self._refresh_variant_note()

        # E3 — one-click month-end bundle.
        wizard_row = QHBoxLayout()
        wizard_btn = QPushButton("Period-Close Wizard…")
        wizard_btn.setToolTip(
            "Generate all four QBO CSVs (invoices, bills, payments, "
            "credits) for a date range plus a manifest README in one\n"
            "step. Uses the CSV variant selected above."
        )
        wizard_btn.clicked.connect(self._open_period_close_wizard)
        wizard_row.addStretch()
        wizard_row.addWidget(wizard_btn)
        layout.addLayout(wizard_row)

        layout.addWidget(tabs, 1)
        self.setLayout(layout)

        self._refresh_health(skip_connect=True)
        self._load_invoices()
        self._load_bills()
        self._load_payments()
        self._load_credits()

    # ─── Variant helpers ─────────────────────────────────────────────

    def _current_variant(self) -> CsvVariant:
        try:
            data = self._variant_combo.currentData()
            return CsvVariant(data) if data else CsvVariant.GENERIC
        except (ValueError, AttributeError):
            return CsvVariant.GENERIC

    def _on_variant_changed(self) -> None:
        v = self._current_variant()
        try:
            set_default_variant(v)
        except Exception:
            pass
        self._refresh_variant_note()

    def _refresh_variant_note(self) -> None:
        try:
            self._variant_note.setText(native_support_note(self._current_variant()))
        except Exception:
            self._variant_note.setText("")

    def _open_period_close_wizard(self) -> None:
        """E3 — launch the month-end Period-Close wizard."""
        try:
            from jaks_inventory.ui.period_close_wizard import PeriodCloseWizard
        except Exception as e:  # pragma: no cover - import failure
            QMessageBox.critical(self, "Period-Close", str(e))
            return
        dlg = PeriodCloseWizard(self)
        if dlg.exec():
            # Refresh all tabs so freshly-marked items leave the lists.
            self._load_invoices()
            self._load_bills()
            self._load_payments()
            self._load_credits()

    # ─── E4: Mark-as-posted override ────────────────────────────────

    def _on_invoice_context_menu(self, pos) -> None:
        idx = self._inv_table.indexAt(pos)
        if not idx.isValid() or not self._invoices:
            return
        row = idx.row()
        if row < 0 or row >= len(self._invoices):
            return
        inv = self._invoices[row]
        menu = QMenu(self._inv_table)
        act_mark = menu.addAction("Mark as posted to QBO (manual)")
        act = menu.exec(self._inv_table.viewport().mapToGlobal(pos))
        if act is act_mark:
            self._do_mark_invoice_posted(inv)

    def _do_mark_invoice_posted(self, inv: dict) -> None:
        inv_no = inv.get("invoice_number") or f"#{inv.get('id')}"
        resp = QMessageBox.question(
            self, "Mark as posted",
            f"Mark invoice {inv_no} as already posted in QuickBooks?\n\n"
            "Use this only when the invoice has been hand-entered or "
            "synced into QBO outside this app. JAKS will stop showing "
            "it on the unexported list.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        try:
            from db import mark_invoice_posted_manually, log_qbo_export
            ok = mark_invoice_posted_manually(int(inv["id"]))
            if not ok:
                QMessageBox.warning(self, "Mark as posted",
                                    "Invoice not found in database.")
                return
            try:
                log_qbo_export("invoices", "manual", 1,
                               f"manual mark: {inv_no}")
            except Exception:
                pass
            self._load_invoices()
        except Exception as e:  # pragma: no cover - user-facing
            QMessageBox.critical(self, "Mark as posted", str(e))

    def _on_bill_context_menu(self, pos) -> None:
        idx = self._bill_table.indexAt(pos)
        if not idx.isValid() or not self._bills:
            return
        row = idx.row()
        if row < 0 or row >= len(self._bills):
            return
        po = self._bills[row]
        menu = QMenu(self._bill_table)
        act_mark = menu.addAction("Mark as posted to QBO (manual)")
        act = menu.exec(self._bill_table.viewport().mapToGlobal(pos))
        if act is act_mark:
            self._do_mark_po_posted(po)

    def _do_mark_po_posted(self, po: dict) -> None:
        po_no = po.get("po_number") or f"#{po.get('id')}"
        resp = QMessageBox.question(
            self, "Mark as posted",
            f"Mark PO {po_no} as already posted as a Bill in QuickBooks?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        try:
            from db import mark_po_posted_manually, log_qbo_export
            ok = mark_po_posted_manually(int(po["id"]))
            if not ok:
                QMessageBox.warning(self, "Mark as posted",
                                    "PO not found in database.")
                return
            try:
                log_qbo_export("bills", "manual", 1, f"manual mark: {po_no}")
            except Exception:
                pass
            self._load_bills()
        except Exception as e:  # pragma: no cover - user-facing
            QMessageBox.critical(self, "Mark as posted", str(e))

    def _warn_if_non_native(self, entity: str) -> bool:
        """Show a one-time confirmation when exporting an entity the
        QBO Online native importer doesn't support. Returns True to
        proceed, False to cancel.
        """
        if self._current_variant() != CsvVariant.QBO_ONLINE:
            return True
        if SUPPORTED_NATIVELY.get(entity, False):
            return True
        resp = QMessageBox.warning(
            self,
            "Not natively importable",
            f"QuickBooks Online's native importer does not accept "
            f"{entity} CSVs. You will need a third-party importer "
            "(SaasAnt, Transaction Pro, Business Importer) to load this "
            "file, or hand-enter the data.\n\nContinue with export?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        return resp == QMessageBox.StandardButton.Yes

    # ─── Pre-export validation ───────────────────────────────────────

    def _run_validation(self, records: list[dict], kind: str) -> bool:
        """Run :func:`qbo.validate.validate_for_export` and surface the
        report. Returns True if the caller should proceed.

        - No issues → returns True silently.
        - Warnings only → confirm dialog (proceed = True).
        - Errors → modal block; user can override with "Export anyway".
        """
        try:
            report: ValidationReport = validate_for_export(records, kind)
        except Exception:
            return True  # never let validation hard-fail a user action

        if report.total == 0:
            return True

        title = "Pre-export validation"
        body = report.as_text()
        if report.is_blocking:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Critical)
            box.setWindowTitle(title)
            box.setText(
                f"Found {len(report.errors)} error(s) and "
                f"{len(report.warnings)} warning(s)."
            )
            box.setDetailedText(body)
            override = box.addButton("Export anyway",
                                     QMessageBox.ButtonRole.DestructiveRole)
            cancel = box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(cancel)
            box.exec()
            return box.clickedButton() is override

        # warnings-only
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setText(f"{len(report.warnings)} warning(s) — review before exporting.")
        box.setDetailedText(body)
        proceed = box.addButton("Continue", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(proceed)
        box.exec()
        return box.clickedButton() is proceed

    # ─── Invoice tab ──────────────────────────────────────────────────

    def _build_invoice_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout()

        controls = QHBoxLayout()
        self._inv_start = QDateEdit(); self._inv_start.setCalendarPopup(True)
        self._inv_start.setDate(QDate.currentDate().addMonths(-1))
        self._inv_end = QDateEdit(); self._inv_end.setCalendarPopup(True)
        self._inv_end.setDate(QDate.currentDate())
        refresh = QPushButton("Reload Unexported")
        refresh.clicked.connect(self._load_invoices)
        export_btn = QPushButton("Export CSV")
        export_btn.clicked.connect(self._export_invoices)
        push_btn = QPushButton("Push to QBO")
        push_btn.setToolTip(
            "Send selected invoices directly to QuickBooks Online via\n"
            "the live API. Requires QBO credentials and 'Enable QBO\n"
            "Write Operations' in Settings → QBO. Mock mode returns\n"
            "simulated success so you can dry-run safely."
        )
        push_btn.clicked.connect(self._push_invoices)
        controls.addWidget(QLabel("From:")); controls.addWidget(self._inv_start)
        controls.addWidget(QLabel("To:")); controls.addWidget(self._inv_end)
        controls.addWidget(refresh)
        controls.addStretch()
        controls.addWidget(export_btn)
        controls.addWidget(push_btn)
        lay.addLayout(controls)

        self._inv_count = QLabel("")
        self._inv_count.setStyleSheet("font-size: 11pt; padding: 4px;")
        lay.addWidget(self._inv_count)

        self._inv_table = QTableWidget(0, 5)
        self._inv_table.setHorizontalHeaderLabels(
            ["Invoice #", "Date", "Customer", "Status", "Total"]
        )
        self._inv_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._inv_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._inv_table.setSelectionMode(
            QAbstractItemView.SelectionMode.MultiSelection
        )
        self._inv_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self._inv_table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._inv_table.customContextMenuRequested.connect(
            self._on_invoice_context_menu
        )
        lay.addWidget(self._inv_table, 1)

        tab.setLayout(lay)
        return tab

    def _load_invoices(self) -> None:
        try:
            self._invoices = get_unexported_invoices(
                _date_str(self._inv_start.date()),
                _date_str(self._inv_end.date()),
            )
        except Exception:
            self._invoices = []
        self._inv_count.setText(
            f"{len(self._invoices)} unexported invoice(s)"
        )
        self._inv_table.setRowCount(len(self._invoices))
        for i, inv in enumerate(self._invoices):
            cust = inv.get("customer_company") or inv.get("customer_name") or "—"
            total = float(inv.get("total") or inv.get("subtotal") or 0)
            self._inv_table.setItem(i, 0, _ro_item(inv.get("invoice_number", "")))
            self._inv_table.setItem(i, 1, _ro_item(str(inv.get("invoice_date", ""))[:10]))
            self._inv_table.setItem(i, 2, _ro_item(cust))
            self._inv_table.setItem(i, 3, _ro_item(inv.get("status", "")))
            self._inv_table.setItem(i, 4, _ro_item(f"${total:,.2f}"))

    def _selected_indices(self, table: QTableWidget, fallback_count: int) -> list[int]:
        rows = sorted({idx.row() for idx in table.selectedIndexes()})
        return rows or list(range(fallback_count))

    def _export_invoices(self) -> None:
        if not self._invoices:
            QMessageBox.information(self, "No Data", "No invoices to export.")
            return
        if not self._warn_if_non_native("invoice"):
            return
        rows = self._selected_indices(self._inv_table, len(self._invoices))
        summaries = [self._invoices[r] for r in rows]
        path = self._ask_save_path("invoices_export")
        if not path:
            return
        try:
            full = [get_invoice(int(s["id"])) or s for s in summaries]
            if not self._run_validation(full, "invoice"):
                return
            try:
                from db import get_qbo_tax_code_for_rate as _tax_resolver
            except Exception:
                _tax_resolver = None
            line_rows = write_invoices_csv(
                path, full,
                tax_code_resolver=_tax_resolver,
                variant=self._current_variant(),
            )
            ids = [s.get("id") for s in summaries if s.get("id")]
            mark_invoices_exported(ids)
            log_qbo_export("invoices", "csv", len(summaries), path)
            QMessageBox.information(
                self, "Exported",
                f"Exported {len(summaries)} invoice(s) "
                f"({line_rows} line row(s)) to:\n{path}"
            )
            self._load_invoices()
        except Exception as e:  # pragma: no cover - user-facing
            QMessageBox.critical(self, "Export Error", str(e))

    def _push_invoices(self) -> None:
        """D3 — push selected invoices to QBO via the live API."""
        if not self._invoices:
            QMessageBox.information(self, "No Data", "No invoices to push.")
            return
        rows = self._selected_indices(self._inv_table, len(self._invoices))
        summaries = [self._invoices[r] for r in rows]
        if not self._confirm_push(len(summaries), "invoice"):
            return
        try:
            from qbo.push import push_invoices_to_qbo
            from db import get_db
            full = [get_invoice(int(s["id"])) or s for s in summaries]

            def _stamp(invoice_id, qbo_id):
                with get_db() as conn:
                    conn.execute(
                        "UPDATE invoices SET qbo_invoice_id=%s, "
                        "qbo_exported=1, qbo_exported_at=CURRENT_TIMESTAMP "
                        "WHERE id=%s",
                        (qbo_id, int(invoice_id)),
                    )
                    conn.commit()

            result = push_invoices_to_qbo(full, on_success=_stamp)
            log_qbo_export("invoices", "api", result["ok"], "(QBO API)")
            self._show_push_summary("invoices", result)
            self._load_invoices()
        except Exception as e:  # pragma: no cover - user-facing
            QMessageBox.critical(self, "QBO Push Error", str(e))

    def _build_bill_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout()

        controls = QHBoxLayout()
        self._bill_start = QDateEdit(); self._bill_start.setCalendarPopup(True)
        self._bill_start.setDate(QDate.currentDate().addMonths(-1))
        self._bill_end = QDateEdit(); self._bill_end.setCalendarPopup(True)
        self._bill_end.setDate(QDate.currentDate())
        refresh = QPushButton("Reload Unexported")
        refresh.clicked.connect(self._load_bills)
        export_btn = QPushButton("Export CSV")
        export_btn.clicked.connect(self._export_bills)
        push_btn = QPushButton("Push to QBO")
        push_btn.setToolTip(
            "Send selected POs directly to QuickBooks Online as Bills\n"
            "via the live API. Requires QBO credentials and 'Enable\n"
            "QBO Write Operations' in Settings → QBO."
        )
        push_btn.clicked.connect(self._push_bills)
        controls.addWidget(QLabel("From:")); controls.addWidget(self._bill_start)
        controls.addWidget(QLabel("To:")); controls.addWidget(self._bill_end)
        controls.addWidget(refresh)
        controls.addStretch()
        controls.addWidget(export_btn)
        controls.addWidget(push_btn)
        lay.addLayout(controls)

        self._bill_count = QLabel("")
        self._bill_count.setStyleSheet("font-size: 11pt; padding: 4px;")
        lay.addWidget(self._bill_count)

        self._bill_table = QTableWidget(0, 5)
        self._bill_table.setHorizontalHeaderLabels(
            ["PO #", "Date", "Vendor", "Status", "Total"]
        )
        self._bill_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._bill_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._bill_table.setSelectionMode(
            QAbstractItemView.SelectionMode.MultiSelection
        )
        self._bill_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self._bill_table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._bill_table.customContextMenuRequested.connect(
            self._on_bill_context_menu
        )
        lay.addWidget(self._bill_table, 1)

        tab.setLayout(lay)
        return tab

    def _load_bills(self) -> None:
        try:
            self._bills = get_unexported_bills(
                _date_str(self._bill_start.date()),
                _date_str(self._bill_end.date()),
            )
        except Exception:
            self._bills = []
        self._bill_count.setText(
            f"{len(self._bills)} unexported bill(s) / PO(s)"
        )
        self._bill_table.setRowCount(len(self._bills))
        for i, b in enumerate(self._bills):
            total = float(b.get("total") or 0)
            self._bill_table.setItem(i, 0, _ro_item(b.get("po_number", "")))
            self._bill_table.setItem(i, 1, _ro_item(str(b.get("order_date", ""))[:10]))
            self._bill_table.setItem(i, 2, _ro_item(b.get("vendor_name") or "—"))
            self._bill_table.setItem(i, 3, _ro_item(b.get("status", "")))
            self._bill_table.setItem(i, 4, _ro_item(f"${total:,.2f}"))

    def _export_bills(self) -> None:
        if not self._bills:
            QMessageBox.information(self, "No Data", "No bills to export.")
            return
        if not self._warn_if_non_native("bill"):
            return
        rows = self._selected_indices(self._bill_table, len(self._bills))
        summaries = [self._bills[r] for r in rows]
        path = self._ask_save_path("bills_export")
        if not path:
            return
        try:
            full = [get_purchase_order(int(s["id"])) or s for s in summaries]
            if not self._run_validation(full, "bill"):
                return
            line_rows = write_bills_csv(path, full, variant=self._current_variant())
            ids = [s.get("id") for s in summaries if s.get("id")]
            mark_pos_exported(ids)
            log_qbo_export("bills", "csv", len(summaries), path)
            QMessageBox.information(
                self, "Exported",
                f"Exported {len(summaries)} bill(s) "
                f"({line_rows} line row(s)) to:\n{path}"
            )
            self._load_bills()
        except Exception as e:  # pragma: no cover - user-facing
            QMessageBox.critical(self, "Export Error", str(e))

    def _push_bills(self) -> None:
        """D3 — push selected POs to QBO as Bills via the live API."""
        if not self._bills:
            QMessageBox.information(self, "No Data", "No bills to push.")
            return
        rows = self._selected_indices(self._bill_table, len(self._bills))
        summaries = [self._bills[r] for r in rows]
        if not self._confirm_push(len(summaries), "bill"):
            return
        try:
            from qbo.push import push_bills_to_qbo
            from db import get_db
            full = [get_purchase_order(int(s["id"])) or s for s in summaries]

            def _stamp(po_id, qbo_id):
                with get_db() as conn:
                    conn.execute(
                        "UPDATE purchase_orders SET qbo_bill_id=%s, "
                        "qbo_exported=1, qbo_exported_at=CURRENT_TIMESTAMP "
                        "WHERE id=%s",
                        (qbo_id, int(po_id)),
                    )
                    conn.commit()

            result = push_bills_to_qbo(full, on_success=_stamp)
            log_qbo_export("bills", "api", result["ok"], "(QBO API)")
            self._show_push_summary("bills", result)
            self._load_bills()
        except Exception as e:  # pragma: no cover - user-facing
            QMessageBox.critical(self, "QBO Push Error", str(e))

    def _build_payment_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout()

        controls = QHBoxLayout()
        self._pay_start = QDateEdit(); self._pay_start.setCalendarPopup(True)
        self._pay_start.setDate(QDate.currentDate().addMonths(-1))
        self._pay_end = QDateEdit(); self._pay_end.setCalendarPopup(True)
        self._pay_end.setDate(QDate.currentDate())
        refresh = QPushButton("Reload Payments")
        refresh.clicked.connect(self._load_payments)
        export_btn = QPushButton("Export CSV")
        export_btn.clicked.connect(self._export_payments)
        push_btn = QPushButton("Push to QBO")
        push_btn.setToolTip(
            "Send selected payments to QuickBooks Online as\n"
            "ReceivePayment records via the live API. The matching\n"
            "invoice must already have been pushed to QBO so the\n"
            "payment can be linked."
        )
        push_btn.clicked.connect(self._push_payments)
        controls.addWidget(QLabel("From:")); controls.addWidget(self._pay_start)
        controls.addWidget(QLabel("To:")); controls.addWidget(self._pay_end)
        controls.addWidget(refresh)
        controls.addStretch()
        controls.addWidget(export_btn)
        controls.addWidget(push_btn)
        lay.addLayout(controls)

        self._pay_count = QLabel("")
        self._pay_count.setStyleSheet("font-size: 11pt; padding: 4px;")
        lay.addWidget(self._pay_count)

        self._pay_table = QTableWidget(0, 5)
        self._pay_table.setHorizontalHeaderLabels(
            ["Invoice #", "Date", "Customer", "Amount Paid", "Method"]
        )
        self._pay_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._pay_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._pay_table.setSelectionMode(
            QAbstractItemView.SelectionMode.MultiSelection
        )
        self._pay_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        lay.addWidget(self._pay_table, 1)

        tab.setLayout(lay)
        return tab

    def _load_payments(self) -> None:
        try:
            self._payments = get_paid_invoices_for_export(
                _date_str(self._pay_start.date()),
                _date_str(self._pay_end.date()),
            )
        except Exception:
            self._payments = []
        self._pay_count.setText(
            f"{len(self._payments)} paid invoice(s) in range"
        )
        self._pay_table.setRowCount(len(self._payments))
        for i, p in enumerate(self._payments):
            cust = p.get("customer_company") or p.get("customer_name") or "—"
            amt = float(p.get("amount_paid") or 0)
            self._pay_table.setItem(i, 0, _ro_item(p.get("invoice_number", "")))
            self._pay_table.setItem(i, 1, _ro_item(str(p.get("invoice_date", ""))[:10]))
            self._pay_table.setItem(i, 2, _ro_item(cust))
            self._pay_table.setItem(i, 3, _ro_item(f"${amt:,.2f}"))
            self._pay_table.setItem(i, 4, _ro_item(p.get("payment_method") or ""))

    def _export_payments(self) -> None:
        if not self._payments:
            QMessageBox.information(self, "No Data", "No payments to export.")
            return
        if not self._warn_if_non_native("payment"):
            return
        rows = self._selected_indices(self._pay_table, len(self._payments))
        to_export = [self._payments[r] for r in rows]
        if not self._run_validation(to_export, "payment"):
            return
        path = self._ask_save_path("payments_export")
        if not path:
            return
        try:
            line_rows = write_payments_csv(path, to_export, variant=self._current_variant())
            log_qbo_export("payments", "csv", line_rows, path)
            QMessageBox.information(
                self, "Exported",
                f"Exported {line_rows} payment row(s) to:\n{path}"
            )
        except Exception as e:  # pragma: no cover - user-facing
            QMessageBox.critical(self, "Export Error", str(e))

    def _push_payments(self) -> None:
        """D4 — push selected payments to QBO via the live API."""
        if not self._payments:
            QMessageBox.information(self, "No Data", "No payments to push.")
            return
        rows = self._selected_indices(self._pay_table, len(self._payments))
        to_push = [self._payments[r] for r in rows]
        if not self._confirm_push(len(to_push), "payment"):
            return
        try:
            from qbo.push import push_payments_to_qbo
            from db import get_db

            def _stamp(invoice_id, qbo_id):
                with get_db() as conn:
                    conn.execute(
                        "UPDATE invoices SET qbo_payment_id=%s WHERE id=%s",
                        (qbo_id, int(invoice_id)),
                    )
                    conn.commit()

            result = push_payments_to_qbo(to_push, on_success=_stamp)
            log_qbo_export("payments", "api", result["ok"], "(QBO API)")
            self._show_push_summary("payments", result)
            self._load_payments()
        except Exception as e:  # pragma: no cover - user-facing
            QMessageBox.critical(self, "QBO Push Error", str(e))

    # ─── Credit-memo tab ─────────────────────────────────────────────

    def _build_credit_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout()

        controls = QHBoxLayout()
        self._cred_start = QDateEdit(); self._cred_start.setCalendarPopup(True)
        self._cred_start.setDate(QDate.currentDate().addMonths(-1))
        self._cred_end = QDateEdit(); self._cred_end.setCalendarPopup(True)
        self._cred_end.setDate(QDate.currentDate())
        refresh = QPushButton("Reload Credits")
        refresh.clicked.connect(self._load_credits)
        export_btn = QPushButton("Export CSV")
        export_btn.clicked.connect(self._export_credits)
        push_btn = QPushButton("Push to QBO")
        push_btn.setToolTip(
            "Send selected credit memos to QuickBooks Online via\n"
            "the live API. Voided credits are skipped automatically."
        )
        push_btn.clicked.connect(self._push_credits)
        controls.addWidget(QLabel("From:")); controls.addWidget(self._cred_start)
        controls.addWidget(QLabel("To:")); controls.addWidget(self._cred_end)
        controls.addWidget(refresh)
        controls.addStretch()
        controls.addWidget(export_btn)
        controls.addWidget(push_btn)
        lay.addLayout(controls)

        self._cred_count = QLabel("")
        self._cred_count.setStyleSheet("font-size: 11pt; padding: 4px;")
        lay.addWidget(self._cred_count)

        self._cred_table = QTableWidget(0, 6)
        self._cred_table.setHorizontalHeaderLabels(
            ["ID", "Issued", "Customer", "Source", "Amount", "Status"]
        )
        self._cred_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._cred_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._cred_table.setSelectionMode(
            QAbstractItemView.SelectionMode.MultiSelection
        )
        self._cred_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        lay.addWidget(self._cred_table, 1)

        tab.setLayout(lay)
        return tab

    def _load_credits(self) -> None:
        try:
            self._credits = get_credit_memos_for_export(
                start_date=_date_str(self._cred_start.date()),
                end_date=_date_str(self._cred_end.date()),
            )
        except Exception:
            self._credits = []
        self._cred_count.setText(
            f"{len(self._credits)} credit memo(s) in range"
        )
        self._cred_table.setRowCount(len(self._credits))
        for i, c in enumerate(self._credits):
            cust = c.get("customer_company") or c.get("customer_name") or "—"
            amt = float(c.get("amount") or 0)
            self._cred_table.setItem(i, 0, _ro_item(c.get("id", "")))
            self._cred_table.setItem(i, 1, _ro_item(str(c.get("issue_date", ""))[:10]))
            self._cred_table.setItem(i, 2, _ro_item(cust))
            self._cred_table.setItem(i, 3, _ro_item(c.get("source_type") or ""))
            self._cred_table.setItem(i, 4, _ro_item(f"${amt:,.2f}"))
            self._cred_table.setItem(i, 5, _ro_item(c.get("status") or ""))

    def _export_credits(self) -> None:
        if not self._credits:
            QMessageBox.information(self, "No Data", "No credits to export.")
            return
        if not self._warn_if_non_native("credit"):
            return
        rows = self._selected_indices(self._cred_table, len(self._credits))
        to_export = [self._credits[r] for r in rows]
        if not self._run_validation(to_export, "credit"):
            return
        path = self._ask_save_path("credit_memos_export")
        if not path:
            return
        try:
            line_rows = write_credit_memos_csv(path, to_export, variant=self._current_variant())
            log_qbo_export("credit_memos", "csv", line_rows, path)
            QMessageBox.information(
                self, "Exported",
                f"Exported {line_rows} credit-memo row(s) to:\n{path}"
            )
        except Exception as e:  # pragma: no cover - user-facing
            QMessageBox.critical(self, "Export Error", str(e))

    def _push_credits(self) -> None:
        """D4 — push selected credit memos to QBO via the live API."""
        if not self._credits:
            QMessageBox.information(self, "No Data", "No credits to push.")
            return
        rows = self._selected_indices(self._cred_table, len(self._credits))
        to_push = [self._credits[r] for r in rows]
        if not self._confirm_push(len(to_push), "credit memo"):
            return
        try:
            from qbo.push import push_credits_to_qbo
            from db import get_db

            def _stamp(credit_id, qbo_id):
                with get_db() as conn:
                    conn.execute(
                        "UPDATE customer_credits SET qbo_credit_id=%s "
                        "WHERE id=%s",
                        (qbo_id, int(credit_id)),
                    )
                    conn.commit()

            result = push_credits_to_qbo(to_push, on_success=_stamp)
            log_qbo_export("credit_memos", "api", result["ok"], "(QBO API)")
            self._show_push_summary("credit memos", result)
            self._load_credits()
        except Exception as e:  # pragma: no cover - user-facing
            QMessageBox.critical(self, "QBO Push Error", str(e))

    # ─── History tab ──────────────────────────────────────────────────

    def _build_history_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout()

        controls = QHBoxLayout()
        refresh = QPushButton("Refresh History")
        refresh.clicked.connect(self._load_history)
        from PySide6.QtWidgets import QLineEdit, QCheckBox
        self._hist_search = QLineEdit()
        self._hist_search.setPlaceholderText(
            "Filter by type, format, or file path…"
        )
        self._hist_search.textChanged.connect(self._apply_history_filter)
        self._hist_show_failures = QCheckBox("Show QBO failures")
        self._hist_show_failures.setChecked(True)
        self._hist_show_failures.toggled.connect(self._load_history)
        controls.addWidget(refresh)
        controls.addWidget(QLabel("Search:"))
        controls.addWidget(self._hist_search, 1)
        controls.addWidget(self._hist_show_failures)
        lay.addLayout(controls)

        self._hist_table = QTableWidget(0, 6)
        self._hist_table.setHorizontalHeaderLabels(
            ["Date", "Type", "Format", "Records", "File / Detail", "Status"]
        )
        self._hist_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._hist_table.setSortingEnabled(True)
        self._hist_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch
        )
        lay.addWidget(self._hist_table, 1)
        tab.setLayout(lay)
        self._load_history()
        return tab

    def _load_history(self) -> None:
        # Pull both export-log rows and failed sync-log rows into a
        # unified timeline so the user sees CSV exports, API pushes,
        # AND idempotent_write failures in one place.
        rows: list[dict] = []
        try:
            for h in get_export_history():
                rows.append({
                    "created_at": str(h.get("created_at", ""))[:19],
                    "type": h.get("export_type", ""),
                    "format": h.get("export_format", ""),
                    "records": int(h.get("record_count") or 0),
                    "detail": h.get("file_path", ""),
                    "status": "ok",
                })
        except Exception:
            pass

        if self._hist_show_failures.isChecked():
            try:
                from db import get_db
                with get_db() as conn:
                    sync_rows = conn.execute(
                        "SELECT entity_type, local_ref, status, attempts, "
                        "last_error, updated_at FROM qbo_sync_log "
                        "WHERE status = 'failed' "
                        "ORDER BY updated_at DESC LIMIT 200"
                    ).fetchall()
                for r in sync_rows:
                    d = dict(r) if hasattr(r, "keys") else dict(enumerate(r))
                    rows.append({
                        "created_at": str(
                            d.get("updated_at") or d.get(5) or ""
                        )[:19],
                        "type": str(d.get("entity_type") or d.get(0) or ""),
                        "format": "api",
                        "records": int(d.get("attempts") or d.get(3) or 0),
                        "detail": (
                            f"{d.get('local_ref') or d.get(1) or ''}: "
                            f"{(d.get('last_error') or d.get(4) or '')[:120]}"
                        ),
                        "status": "failed",
                    })
            except Exception:
                pass

        # Newest first.
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        self._hist_rows = rows
        self._apply_history_filter()

    def _apply_history_filter(self) -> None:
        needle = self._hist_search.text().strip().lower() if hasattr(
            self, "_hist_search"
        ) else ""
        rows = getattr(self, "_hist_rows", [])
        if needle:
            filt = [
                r for r in rows
                if needle in r["type"].lower()
                or needle in r["format"].lower()
                or needle in r["detail"].lower()
            ]
        else:
            filt = rows
        # Sorting must be temporarily disabled while we mutate the rows
        # or QTableWidget re-orders mid-insert and corrupts indexes.
        self._hist_table.setSortingEnabled(False)
        self._hist_table.setRowCount(len(filt))
        for i, r in enumerate(filt):
            self._hist_table.setItem(i, 0, _ro_item(r["created_at"]))
            self._hist_table.setItem(i, 1, _ro_item(r["type"]))
            self._hist_table.setItem(i, 2, _ro_item(r["format"]))
            self._hist_table.setItem(i, 3, _ro_item(r["records"]))
            self._hist_table.setItem(i, 4, _ro_item(r["detail"]))
            status_item = _ro_item(r["status"])
            if r["status"] == "failed":
                status_item.setForeground(Qt.GlobalColor.red)
            self._hist_table.setItem(i, 5, status_item)
        self._hist_table.setSortingEnabled(True)

    def _refresh_health(self, *, skip_connect: bool = True) -> None:
        """D5 — pull `qbo.health.get_qbo_health` and update the banner."""
        try:
            from qbo.health import get_qbo_health
            h = get_qbo_health(skip_connect=skip_connect)
        except Exception as e:
            self._health_label.setText(f"QBO status: error ({e})")
            return
        parts = [f"<b>{h['label']}</b>"]
        if h.get("environment"):
            parts.append(h["environment"])
        if h.get("realm_id"):
            parts.append(f"realm {h['realm_id']}")
        if h.get("last_export_at"):
            parts.append(f"last export {str(h['last_export_at'])[:16]}")
        if h.get("last_error"):
            parts.append(
                f"<span style='color:#c00;'>last error: "
                f"{h['last_error'][:80]}</span>"
            )
        self._health_label.setText("   ·   ".join(parts))

    # ─── Audit Log tab (every QBO request/response) ───────────────────

    def _build_audit_tab(self) -> QWidget:
        from PySide6.QtWidgets import QCheckBox

        tab = QWidget()
        lay = QVBoxLayout()

        # Filter row
        filt = QHBoxLayout()
        filt.addWidget(QLabel("Source:"))
        self._audit_source = QComboBox()
        self._audit_source.addItem("All", "")
        for label in ("invoice", "bill", "payment", "credit_memo",
                      "journal_entry", "customer", "vendor"):
            self._audit_source.addItem(label, label)
        filt.addWidget(self._audit_source)

        filt.addWidget(QLabel("Status:"))
        self._audit_status = QComboBox()
        self._audit_status.addItem("All", "")
        for label in ("success", "failed", "skipped", "pending"):
            self._audit_status.addItem(label, label)
        filt.addWidget(self._audit_status)

        filt.addWidget(QLabel("Env:"))
        self._audit_env = QComboBox()
        self._audit_env.addItem("All", "")
        for label in ("mock", "sandbox", "production"):
            self._audit_env.addItem(label, label)
        filt.addWidget(self._audit_env)

        self._audit_show_resolved = QCheckBox("Show resolved")
        self._audit_show_resolved.setChecked(True)
        filt.addWidget(self._audit_show_resolved)

        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._load_audit)
        filt.addWidget(refresh)
        filt.addStretch(1)
        lay.addLayout(filt)

        # Wire filter changes
        self._audit_source.currentIndexChanged.connect(self._load_audit)
        self._audit_status.currentIndexChanged.connect(self._load_audit)
        self._audit_env.currentIndexChanged.connect(self._load_audit)
        self._audit_show_resolved.toggled.connect(self._load_audit)

        # Table
        self._audit_table = QTableWidget(0, 8)
        self._audit_table.setHorizontalHeaderLabels([
            "Time", "Env", "Source", "QBO Object",
            "Status", "QBO ID", "HTTP", "Error",
        ])
        self._audit_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._audit_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._audit_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._audit_table.horizontalHeader().setSectionResizeMode(
            7, QHeaderView.ResizeMode.Stretch
        )
        self._audit_table.doubleClicked.connect(self._open_audit_entry)
        lay.addWidget(self._audit_table, 1)

        # Action row
        actions = QHBoxLayout()
        view_btn = QPushButton("View Details…")
        view_btn.clicked.connect(self._open_audit_entry)
        actions.addWidget(view_btn)
        actions.addStretch(1)
        lay.addLayout(actions)

        tab.setLayout(lay)
        self._load_audit()
        return tab

    def _load_audit(self) -> None:
        try:
            from qbo import sync_log
            rows = sync_log.list_logs(
                limit=300,
                source_type=(self._audit_source.currentData() or None),
                status=(self._audit_status.currentData() or None),
                environment=(self._audit_env.currentData() or None),
                include_resolved=self._audit_show_resolved.isChecked(),
            )
        except Exception as exc:
            rows = []
            QMessageBox.warning(self, "Audit Log",
                                f"Failed to load audit log: {exc}")

        self._audit_rows = rows
        self._audit_table.setSortingEnabled(False)
        self._audit_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            d = dict(r) if hasattr(r, "keys") else r
            self._audit_table.setItem(i, 0, _ro_item(str(d.get("created_at", ""))[:19]))
            self._audit_table.setItem(i, 1, _ro_item(d.get("environment") or ""))
            self._audit_table.setItem(i, 2, _ro_item(d.get("source_type") or ""))
            self._audit_table.setItem(i, 3, _ro_item(d.get("qbo_object_type") or ""))
            status = d.get("status") or ""
            status_item = _ro_item(status)
            if status == "failed":
                status_item.setForeground(Qt.GlobalColor.red)
            elif status == "success":
                status_item.setForeground(Qt.GlobalColor.darkGreen)
            self._audit_table.setItem(i, 4, status_item)
            self._audit_table.setItem(i, 5, _ro_item(d.get("qbo_object_id") or d.get("qbo_id") or ""))
            self._audit_table.setItem(i, 6, _ro_item(d.get("response_status") or ""))
            self._audit_table.setItem(i, 7, _ro_item((d.get("error_message") or "")[:200]))
        self._audit_table.setSortingEnabled(True)

    def _selected_audit_log_id(self) -> int | None:
        row = self._audit_table.currentRow()
        if row < 0 or row >= len(getattr(self, "_audit_rows", [])):
            return None
        r = self._audit_rows[row]
        d = dict(r) if hasattr(r, "keys") else r
        try:
            return int(d.get("id"))
        except (TypeError, ValueError):
            return None

    def _open_audit_entry(self) -> None:
        log_id = self._selected_audit_log_id()
        if log_id is None:
            QMessageBox.information(self, "Audit Log", "Select a row first.")
            return
        try:
            from jaks_inventory.ui.qbo_audit_entry_dialog import QBOAuditEntryDialog
        except Exception as exc:
            QMessageBox.warning(self, "Audit Log", f"Failed to open dialog: {exc}")
            return
        dlg = QBOAuditEntryDialog(log_id, parent=self)
        dlg.exec()
        # Refresh in case the user did Mark Resolved / Read Back / Retry.
        self._load_audit()

    # ─── Shared helpers ───────────────────────────────────────────────

    def _ask_save_path(self, prefix: str) -> str:
        default = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save CSV", default, "CSV Files (*.csv)"
        )
        return path

    def _confirm_push(self, count: int, kind: str) -> bool:
        """Confirm a live-API push, surfacing the current QBO mode."""
        try:
            from qbo.config import is_mock_mode, is_write_enabled
            mock = is_mock_mode()
            write = is_write_enabled()
        except Exception:
            mock = True
            write = False
        if mock:
            mode = "MOCK MODE — no records will be created in QBO."
        elif not write:
            mode = (
                "Write operations are DISABLED — records will be "
                "validated and skipped without being created."
            )
        else:
            mode = "LIVE WRITE MODE — records will be created in QBO."
        plural = f"{kind}s" if count != 1 else kind
        ret = QMessageBox.question(
            self, f"Push {plural.title()} to QBO",
            f"Push {count} {plural} to QuickBooks Online?\n\n{mode}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return ret == QMessageBox.StandardButton.Yes

    def _show_push_summary(self, label: str, result: dict) -> None:
        """Display a per-record push outcome dialog."""
        ok = result.get("ok", 0)
        skipped = result.get("skipped", 0)
        failed = result.get("failed", 0)
        lines = [
            f"Pushed: {ok}",
            f"Skipped: {skipped}",
            f"Failed: {failed}",
        ]
        # Show first few failures / skip reasons so the user can act.
        for r in result.get("results", []):
            if r.get("status") == "failed":
                lines.append(
                    f"  ✗ id={r.get('id')}: {r.get('error') or 'error'}"
                )
            elif r.get("status") == "skipped" and r.get("reason"):
                lines.append(
                    f"  · id={r.get('id')}: {r.get('reason')}"
                )
            if len(lines) > 18:
                lines.append("  …")
                break
        text = "\n".join(lines)
        if failed:
            QMessageBox.warning(self, f"QBO Push — {label}", text)
        else:
            QMessageBox.information(self, f"QBO Push — {label}", text)
