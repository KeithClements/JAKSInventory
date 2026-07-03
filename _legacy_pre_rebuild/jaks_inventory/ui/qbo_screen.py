"""QBO Reconciliation screen — comparison, repair, and CSV/import tooling.

This screen was renamed from "QBO Export" as part of the QBO UI
restructure. It now positions itself as the **reconciliation** surface:
compare local inventory against QBO, find/repair mismatches, and emit
the CSV exports accountants need outside the live API push flow.

Daily operational sync (push items / push invoices / drain webhook
queue) lives in ACCOUNTING → QBO Sync Center.

Credentials, account mappings, sync authority, and write toggles live
in Settings → QBO.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QTabWidget, QVBoxLayout, QWidget

from .qbo_sync_screen import QBOSyncScreen
from .qbo_export_screen import QBOExportScreen
from .qbo_import_screen import QBOImportScreen


class QBOScreen(QWidget):
    """Unified QuickBooks Online reconciliation screen.

    Tabs are ordered from most-frequent comparison work (Compare &
    Repair) to bookkeeper-grade CSV outputs (CSV Export) to bulk
    inbound reconciliation (Import from QBO).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel("QBO Reconciliation")
        title.setStyleSheet(
            "font-size: 16px; font-weight: 700; color: #1b3a57;"
        )
        subtitle = QLabel(
            "Compare local data against QuickBooks, repair mismatches, "
            "and emit reconciliation CSVs. "
            "<i>Daily push/pull lives in Sync Center · "
            "Credentials and account mapping live in Settings.</i>"
        )
        subtitle.setTextFormat(Qt.TextFormat.RichText)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #555; font-size: 11px;")

        layout.addWidget(title)
        layout.addWidget(subtitle)

        tabs = QTabWidget()
        tabs.addTab(QBOSyncScreen(), "🔍 Compare && Repair")
        tabs.addTab(QBOExportScreen(), "📤 CSV Export")
        tabs.addTab(QBOImportScreen(), "📥 Import from QBO")
        layout.addWidget(tabs, 1)

        self.setLayout(layout)

