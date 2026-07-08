"""Period-Close Wizard — one-click QBO month-end bundle (E3).

Thin Qt wrapper around :func:`qbo.period_close.run_period_close`. Lets
the user pick a calendar month (or custom range), preview totals,
choose an output folder, and generate all four CSVs + manifest in one
shot.
"""
from __future__ import annotations

import os
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from qbo.csv_variants import (
    CsvVariant,
    VARIANT_LABELS,
    get_default_variant,
)
from qbo.period_close import month_range, run_period_close


_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


class PeriodCloseWizard(QDialog):
    """Modal wizard. Pick month or range → choose folder → Run."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("QBO Period-Close")
        self.setMinimumWidth(520)
        self._result_folder: str | None = None

        layout = QVBoxLayout()
        layout.addWidget(QLabel(
            "Generate the four QBO-importable CSVs (invoices, bills, "
            "payments, credit memos) for a date range, plus a manifest "
            "and validation report."
        ))

        form = QFormLayout()

        # Month picker (default mode)
        self._mode_month = QCheckBox("Use whole calendar month")
        self._mode_month.setChecked(True)
        self._mode_month.toggled.connect(self._on_mode_toggled)
        form.addRow(self._mode_month)

        today = date.today()
        self._month_combo = QComboBox()
        for i, name in enumerate(_MONTH_NAMES, start=1):
            self._month_combo.addItem(name, i)
        # Default to previous month (typical month-end use case).
        prev = today.month - 1 or 12
        self._month_combo.setCurrentIndex(prev - 1)
        self._year_spin = QSpinBox()
        self._year_spin.setRange(2000, 2100)
        self._year_spin.setValue(today.year if today.month > 1 else today.year - 1)
        month_row = QHBoxLayout()
        month_row.addWidget(self._month_combo)
        month_row.addWidget(self._year_spin)
        month_row.addStretch()
        form.addRow("Month:", _wrap(month_row))

        # Custom range
        self._start = QDateEdit(); self._start.setCalendarPopup(True)
        self._start.setDate(QDate.currentDate().addMonths(-1))
        self._end = QDateEdit(); self._end.setCalendarPopup(True)
        self._end.setDate(QDate.currentDate())
        form.addRow("Custom from:", self._start)
        form.addRow("Custom to:", self._end)

        # Variant
        self._variant_combo = QComboBox()
        for v in (
            CsvVariant.QBO_ONLINE,
            CsvVariant.SAASANT,
            CsvVariant.TRANSACTION_PRO,
            CsvVariant.GENERIC,
        ):
            self._variant_combo.addItem(VARIANT_LABELS[v], v.value)
        try:
            saved = get_default_variant()
            idx = self._variant_combo.findData(saved.value)
            if idx >= 0:
                self._variant_combo.setCurrentIndex(idx)
        except Exception:
            pass
        form.addRow("CSV variant:", self._variant_combo)

        # Output folder
        self._folder_label = QLabel("(none selected)")
        self._folder_label.setWordWrap(True)
        pick = QPushButton("Choose Folder…")
        pick.clicked.connect(self._on_pick_folder)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self._folder_label, 1)
        folder_row.addWidget(pick)
        form.addRow("Output folder:", _wrap(folder_row))

        # Mark-exported toggle
        self._mark_chk = QCheckBox(
            "Mark included invoices and POs as exported (recommended)"
        )
        self._mark_chk.setChecked(True)
        form.addRow(self._mark_chk)

        layout.addLayout(form)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
        )
        self._run_btn = QPushButton("Generate")
        self._run_btn.setDefault(True)
        self._run_btn.clicked.connect(self._on_run)
        buttons.addButton(self._run_btn,
                          QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)
        self._on_mode_toggled(True)

    # ─── helpers ───────────────────────────────────────────────────

    def _on_mode_toggled(self, checked: bool) -> None:
        self._month_combo.setEnabled(checked)
        self._year_spin.setEnabled(checked)
        self._start.setEnabled(not checked)
        self._end.setEnabled(not checked)

    def _on_pick_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Choose output folder",
            self._folder_label.text() if self._result_folder else os.path.expanduser("~"),
        )
        if path:
            self._result_folder = path
            self._folder_label.setText(path)

    def _resolve_range(self) -> tuple[str, str]:
        if self._mode_month.isChecked():
            return month_range(
                self._year_spin.value(),
                self._month_combo.currentData(),
            )
        return (
            self._start.date().toString("yyyy-MM-dd"),
            self._end.date().toString("yyyy-MM-dd"),
        )

    def _current_variant(self) -> CsvVariant:
        try:
            return CsvVariant(self._variant_combo.currentData())
        except (ValueError, AttributeError):
            return CsvVariant.GENERIC

    def result_folder(self) -> str | None:
        """The folder created by the most recent run, if any."""
        return self._result_folder

    # ─── run ───────────────────────────────────────────────────────

    def _on_run(self) -> None:
        if not self._result_folder:
            QMessageBox.warning(
                self, "No folder", "Pick an output folder first."
            )
            return
        start, end = self._resolve_range()
        if start > end:
            QMessageBox.warning(self, "Invalid range",
                                "Start date must be on or before end date.")
            return

        try:
            from db import (
                get_unexported_invoices,
                get_unexported_bills,
                get_paid_invoices_for_export,
                get_credit_memos_for_export,
                mark_invoices_exported,
                mark_pos_exported,
                log_qbo_export,
                get_invoice,
                get_purchase_order,
            )
            try:
                from db import get_qbo_tax_code_for_rate as _tax_resolver
            except Exception:
                _tax_resolver = None

            res = run_period_close(
                start_date=start,
                end_date=end,
                output_dir=self._result_folder,
                variant=self._current_variant(),
                get_unexported_invoices=get_unexported_invoices,
                get_unexported_bills=get_unexported_bills,
                get_paid_invoices_for_export=get_paid_invoices_for_export,
                get_credit_memos_for_export=get_credit_memos_for_export,
                mark_invoices_exported=mark_invoices_exported,
                mark_pos_exported=mark_pos_exported,
                log_qbo_export=log_qbo_export,
                get_invoice=get_invoice,
                get_purchase_order=get_purchase_order,
                tax_code_resolver=_tax_resolver,
                mark_exported=self._mark_chk.isChecked(),
            )
        except Exception as e:  # pragma: no cover - user-facing
            QMessageBox.critical(self, "Period-Close Error", str(e))
            return

        self._result_folder = res.folder
        msg = (
            f"Wrote {res.counts['invoices']} invoice row(s), "
            f"{res.counts['bills']} bill row(s), "
            f"{res.counts['payments']} payment row(s), "
            f"{res.counts['credit_memos']} credit row(s).\n\n"
            f"Folder:\n{res.folder}"
        )
        if res.validation_blocking:
            msg += (
                "\n\n⚠ Validation errors detected. See "
                "validation_report.txt; invoices/POs were NOT marked "
                "as exported."
            )
        QMessageBox.information(self, "Period-Close complete", msg)
        self.accept()


def _wrap(layout) -> "QDialog":  # tiny helper for QFormLayout
    from PySide6.QtWidgets import QWidget
    w = QWidget()
    w.setLayout(layout)
    return w
