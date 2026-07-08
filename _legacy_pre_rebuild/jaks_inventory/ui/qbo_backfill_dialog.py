"""QBO Historical Backfill Wizard — Phase 7.

Single-dialog wizard that lets the user pull QBO data for a date
range and reconcile local crosswalk columns. Wraps
:func:`qbo.backfill.backfill_many` with a Qt-friendly UI.

Flow:
    1. User picks entity types + date range.
    2. Click "Dry Run" → counts displayed in the results pane.
    3. Click "Run" → live backfill (writes to local DB), counts
       updated, log appended.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


_ENTITY_OPTIONS = [
    ("Customer", True),
    ("Vendor",   True),
    ("Item",     True),
    ("Invoice",  False),
    ("Bill",     False),
    ("Estimate", False),
]


class QBOBackfillWizard(QDialog):
    """Modal dialog driving the QBO historical backfill."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("QBO Historical Backfill")
        self.setMinimumSize(560, 520)
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)

        title = QLabel("QBO Historical Backfill")
        tf = QFont(); tf.setBold(True); tf.setPointSize(14)
        title.setFont(tf)
        root.addWidget(title)

        info = QLabel(
            "Pull QBO data for the selected date range and stamp the "
            "matching QBO Ids onto local rows. Existing crosswalks are "
            "left alone — only blank fields are filled in. Customers, "
            "Vendors and Items ignore the date range (master data)."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #555;")
        root.addWidget(info)

        # Entity checkboxes
        ent_box = QGroupBox("Entities to backfill")
        ent_lay = QVBoxLayout(ent_box)
        self._entity_checks: dict[str, QCheckBox] = {}
        for name, default_on in _ENTITY_OPTIONS:
            cb = QCheckBox(name)
            cb.setChecked(default_on)
            self._entity_checks[name] = cb
            ent_lay.addWidget(cb)
        root.addWidget(ent_box)

        # Date range
        date_box = QGroupBox("Date range (transactions only)")
        date_form = QFormLayout(date_box)
        today = date.today()
        self._start_edit = QDateEdit()
        self._start_edit.setCalendarPopup(True)
        self._start_edit.setDate(today.replace(year=today.year - 1))
        self._end_edit = QDateEdit()
        self._end_edit.setCalendarPopup(True)
        self._end_edit.setDate(today)
        date_form.addRow("Start:", self._start_edit)
        date_form.addRow("End:",   self._end_edit)
        root.addWidget(date_box)

        # Action buttons
        btn_row = QHBoxLayout()
        self._dry_btn = QPushButton("🔍 Dry Run (preview)")
        self._dry_btn.clicked.connect(lambda: self._run(dry_run=True))
        btn_row.addWidget(self._dry_btn)

        self._run_btn = QPushButton("▶ Run (write to DB)")
        self._run_btn.setStyleSheet(
            "QPushButton { background: #2E7D32; color: white; font-weight: bold; "
            "border: none; padding: 6px 14px; border-radius: 3px; }"
            "QPushButton:hover { background: #388E3C; }"
        )
        self._run_btn.clicked.connect(self._on_run_clicked)
        btn_row.addWidget(self._run_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        # Results pane
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText(
            "Results will appear here after a dry run or live run."
        )
        root.addWidget(self._log, 1)

        # Close
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        root.addWidget(bb)

    # ------------------------------------------------------------------
    def _selected_entities(self) -> list[str]:
        return [name for name, cb in self._entity_checks.items() if cb.isChecked()]

    def _on_run_clicked(self) -> None:
        confirm = QMessageBox.question(
            self, "Confirm backfill",
            "This will write QBO Ids onto matching local rows. "
            "Existing crosswalks are preserved.\n\nProceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._run(dry_run=False)

    def _run(self, *, dry_run: bool) -> None:
        entities = self._selected_entities()
        if not entities:
            QMessageBox.information(
                self, "Nothing selected",
                "Tick at least one entity to backfill.",
            )
            return

        start = self._start_edit.date().toPython()
        end = self._end_edit.date().toPython()
        if end < start:
            QMessageBox.warning(
                self, "Invalid date range",
                "End date must be on or after the start date.",
            )
            return

        self._append(
            f"--- {'DRY RUN' if dry_run else 'LIVE RUN'}  "
            f"({start} → {end})  entities={', '.join(entities)} ---"
        )
        self.setEnabled(False)
        try:
            from qbo.backfill import backfill_many
            results = backfill_many(
                entities,
                start_date=start,
                end_date=end,
                dry_run=dry_run,
            )
        except Exception as exc:
            self._append(f"ERROR: {type(exc).__name__}: {exc}")
            self.setEnabled(True)
            return
        finally:
            self.setEnabled(True)

        for et in entities:
            r = results.get(et) or {}
            line = (
                f"  {et:9s}  pages={r.get('pages', 0):3d}  "
                f"updated={r.get('would_update', 0):5d}  "
                f"skipped={r.get('would_skip', 0):5d}"
            )
            errs = r.get("errors") or []
            if errs:
                line += f"  ERRORS={len(errs)}"
            self._append(line)
            for e in errs[:5]:
                self._append(f"      ! {e}")

        self._append("--- done ---")

    def _append(self, text: str) -> None:
        self._log.appendPlainText(text)
